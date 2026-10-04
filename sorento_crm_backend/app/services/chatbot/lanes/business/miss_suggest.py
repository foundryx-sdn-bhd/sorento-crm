"""Port of `sub-miss-suggest` (S6c, AC-607): the lane a MISSED turn takes.

The live graph (`f42de9c6`) has four Code nodes on the did-you-mean spine - `dym-transform`,
`dym-annotate`, `sibling-transform` and the exit `miss-suggest-result` - plus `promo-dym-plan`
on the promotion fan-out. It has NO `build-suggest-offer` of its own: that composer stays on
the SPINE and reads this exit's `outcome_fragment` one hop later (the RS-7 errata), which is
why `build_suggest_offer` lives in `answer.py` and takes the fragment's three members as
parameters.

**Source of truth is the LIVE sub's body**, not the spine's inline copy of the same node
name. `dym-transform` is 561 lines (sha `89bdd18ec79bf82c`) and `dym-annotate` 247 (sha
`a8fc7cdf4887a243`) on `sub-miss-suggest-live@f42de9c6`, verified byte-equal to the export
before porting; the spine ships 421 / 169 of the same names, which are the pre-Fix-4 bodies
and are NOT what the 33 + 10 `sub-miss-suggest-live` captures were graded against.

`dym-transform` and `sub-answer`'s `dym-transform-partial` are ONE body deployed twice - the
node's own header says so - and the normalised diff between them is a short enumerable list
(the `promotion` domain entry, the Fix-4 uuid keying, cross-token dedupe instead of a
five-token cap, `_capForBlock`, and three uuid-keyed output keys). So the planner is written
ONCE here, parametrised by `variant`, and `sub_answer.dym_transform_partial` calls it. Two
independent 500-line copies that must agree byte for byte is exactly how the two lanes'
candidate selection drifts, and the JS itself carries a "keep in lockstep" note on every one
of those functions.

**Nothing here holds a database session.** The two seams that do I/O
(`AnswerServices.mcp_probe`, `.family_fetch`) are injected by the caller.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Mapping

from app.services.chatbot import jsc

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Shared helpers, verbatim from the bodies.
# --------------------------------------------------------------------------- #

# `$` is written `\Z` (Python's `$` also matches before a trailing newline) and `\d` as
# `[0-9]` (Python's `\d` matches every Unicode decimal). Same two rewrites as `gate.py`.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE
)


def _is_uuid(value: Any) -> bool:
    """`UUID_RE.test(String(s || ''))`."""
    return bool(_UUID_RE.match(jsc.js_string(value if jsc.truthy(value) else "")))


def _norm(value: Any) -> str:
    """`String(s ?? '').trim().toLowerCase()`."""
    return jsc.nullish_str(value).strip().lower()


def _cap3(value: Any) -> list:
    """`(a) => (Array.isArray(a) ? a.slice(0, 3) : [])`.

    `answer.py::build_suggest_offer` imports this directly for its OWN re-derived D1
    candidate blocks (a SEPARATE walk of `resolved`/`gate` from this module's own
    `_dym_plan`, which is why the two must not be conflated) - kept here, unchanged,
    for that caller; `_dym_plan`'s own print cut point is `cap_for_block`, which reads
    `roster_caps` instead (AC-1710).
    """
    return list(value[:3]) if isinstance(value, list) else []


def _is_exact(match: Any) -> bool:
    """`String(m?.match_tier || '').toLowerCase() === 'exact'`."""
    tier = jsc.get(match, "match_tier")
    return jsc.js_string(tier if jsc.truthy(tier) else "").lower() == "exact"


def human_label(match: Any) -> Any:
    """`humanLabel`, and the UUID leak guard it exists for.

    A promotion's `canonical_code` IS its uuid, so a candidate with no display name has no
    human label at all and is DROPPED rather than rendered. Shared with `answer.py`'s
    `build_suggest_offer`, whose own copy the JS marks "verbatim from build-suggest-offer".
    """
    code = jsc.get(match, "canonical_code")
    if jsc.truthy(code) and not _is_uuid(code):
        return jsc.js_string(code)
    display = jsc.get(match, "display")
    display = display if jsc.truthy(display) else {}
    for key in ("description", "product_name", "name"):
        value = jsc.get(display, key)
        if jsc.truthy(value):
            return value
    return None


def _flat_matches(resolved: Any) -> list:
    """The resolver flatten both bodies build.

    Resolutions' matches, plus `intersection`, plus every VALUE of `by_entity_type` (H16 -
    the KEYS are entity-type names and are never data). `Object.values(...).flat()`
    flattens one level and passes a non-array element through unchanged.
    """
    flat: list = []
    for res in jsc.array(jsc.get(resolved, "resolutions")):
        matches = jsc.get(res, "matches")
        flat.extend(
            matches if isinstance(matches, list) else ([] if matches is None else [matches])
        )
    flat.extend(jsc.array(jsc.get(resolved, "intersection")))
    by_type = jsc.get(resolved, "by_entity_type")
    if isinstance(by_type, dict):
        for value in by_type.values():
            flat.extend(value if isinstance(value, list) else [value])
    return flat


def gate_resolved_tokens(gate: Any) -> set[str]:
    """The gate's document-class narrowing, honoured by both planners and the composer.

    `r` is bound to the RAW resolver, so a token the GATE resolved still looks unresolved.
    Deliberately narrow: only `document-class-narrowing` and `same-code-collapse` stamps.
    """
    out: set[str] = set()
    for entry in jsc.array(jsc.get(gate, "resolutions")):
        if not jsc.truthy(entry):
            continue
        if jsc.get(entry, "resolved") is not True:
            continue
        if jsc.get(entry, "resolved_by") not in ("document-class-narrowing", "same-code-collapse"):
            continue
        token = jsc.nullish_str(jsc.get(entry, "token")).strip().lower()
        if token:
            out.add(token)
    return out


def gate_incompatible_only_tokens(gate: Any) -> set[str]:
    """D10 (owner console pass, 8 Sep 2026, turn 69d9900e): tokens `gate.py` marked as
    matching NOTHING this domain serves - a flyer/set code hitting `product_set` under
    `incoming`. To this domain the token is exactly as absent as one that matched nothing at
    all, so `miss_resolutions` forces it past the `resolved` / exact-match guards below.
    """
    incompatible = jsc.get(jsc.get(gate, "gate_debug"), "incompatible_only")
    if not isinstance(incompatible, dict):
        return set()
    return {jsc.nullish_str(t).strip().lower() for t in incompatible if jsc.truthy(t)}


def miss_resolutions(resolved: Any, *, gate: Any) -> list:
    """`missResolutions`: a token the resolver did not resolve AND that had no exact match.

    Identical in `dym-transform`, `dym-transform-partial` and `build-suggest-offer`; the
    three bodies carry the same comment demanding they stay identical.
    """
    resolved_tokens = gate_resolved_tokens(gate)
    incompatible_only = gate_incompatible_only_tokens(gate)
    resolutions = jsc.get(resolved, "resolutions")
    if isinstance(resolutions, list):
        out = []
        for res in resolutions:
            if not jsc.truthy(res):
                continue
            token_key = jsc.nullish_str(jsc.get(res, "token")).strip().lower()
            forced_miss = token_key in incompatible_only
            if not forced_miss:
                if jsc.get(res, "resolved") is True:
                    continue
                matches = jsc.get(res, "matches")
                if isinstance(matches, list) and any(_is_exact(m) for m in matches):
                    continue
                if token_key in resolved_tokens:
                    continue
            out.append(res)
        return out
    if jsc.array(jsc.get(resolved, "unresolved_tokens")):
        # legacy single-resolution shape
        return [resolved]
    return []


# --------------------------------------------------------------------------- #
# dym-transform / dym-transform-partial - ONE body, two deployments.
# --------------------------------------------------------------------------- #

# The ONLY place a domain is enabled. `predicate` / `requires` are declared PER DOMAIN on
# purpose: the incoming picker's "a row exists means it has the thing" rule does NOT
# generalise (`crm_inventory_stock_balance_list` returns genuine-zero rows, one per
# warehouse x system location).
#
# `product_attachment` carries NO `probe_cap` (removed 2026-08-22 by the captain; product
# pickers routinely exceed 8 and every code past the cap rendered BARE). `inventory`'s cap
# of 3 is a CORRECTNESS value, not a tuning value: the measured grain is warehouse x system
# location at about 13 rows per candidate, so 5 candidates would saturate the backend's
# 50-row default page every time. Do not raise it without re-measuring the row grain.
DOMAIN_PROBE: dict[str, dict[str, Any]] = {
    "product_attachment": {
        "tool": "crm_master_product_attachments_list",
        "noun": None,  # resolved at render time by attachmentNoun()
        "predicate": "row_present_with_type",
        "requires": ["attachment_type", "certificate"],
    },
    "inventory": {
        "tool": "crm_inventory_stock_balance_list",
        "noun": "stock details",
        "predicate": "qty_gt_zero",  # row presence is NOT has-stock
        "requires": [],
        "probe_cap": 3,
    },
    # `promotion` is on the FULL body only: promotion rows carry no product, so the lane is
    # per-candidate (`row_present`) and `dym-transform-partial` has no such fan-out.
    "promotion": {
        "tool": "crm_marketing_promotions_list",
        "noun": "promotion",
        "predicate": "row_present",
        "requires": [],
        "probe_cap": 3,
    },
}

# `dym-transform-partial`'s own table: the SAME two entries, no `promotion`. The promotion
# lane is per-candidate (`row_present`) and its fan-out nodes live only in `sub-miss-suggest`,
# so on the results lane a promotion turn is `domain_not_enabled` rather than a probe with
# nowhere to run.
PARTIAL_DOMAIN_PROBE: dict[str, dict[str, Any]] = {
    "product_attachment": DOMAIN_PROBE["product_attachment"],
    "inventory": DOMAIN_PROBE["inventory"],
}

# `entity_type` values `sub-get-results`' `entity-ids-transformer` can turn into a narrowing
# `*_ids` filter. An unmappable entity contributes NO narrowing, and
# `crm_inventory_stock_balance_list` spans every product when called with none - so an
# unmappable-only entity set would trigger a full-table read AND label every candidate
# "has stock". Unmappable means not probed.
MAPPABLE: frozenset[str] = frozenset(
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


def _token_candidates(res: Any, *, allowed_types: Any, uuid_keyed: bool) -> list:
    """`tokenCandidates`, parametrised exactly as the live body parametrises it.

    Dedupe key is the CODE for every domain except `product_attachment` on the D1 / picker
    lanes, where it is the UUID - a cross-company code twin is then no longer thrown away
    here and survives as its own candidate, which is the whole point of Fix 4.
    """
    acc: list = []
    matches = jsc.get(res, "matches")
    if isinstance(matches, list):
        acc.extend(matches)
    alternatives = jsc.get(res, "alternatives")
    if isinstance(alternatives, list):
        acc.extend(alternatives)
    seen: list = []
    keep: list = []
    for match in acc:
        code = jsc.get(match, "canonical_code")
        if not jsc.truthy(code):
            continue
        if _is_exact(match):
            continue
        entity_type = jsc.get(match, "entity_type")
        if allowed_types is not None and jsc.truthy(entity_type) and entity_type not in allowed_types:
            continue
        dedupe_key = _norm(jsc.get(match, "uuid")) if uuid_keyed else code
        if not jsc.truthy(dedupe_key):
            # uuid-keyed and genuinely uuid-less: nothing to key on, drop it.
            continue
        if dedupe_key in seen:
            continue
        seen.append(dedupe_key)
        keep.append(match)  # API ranks variants-first / by similarity: keep the order
    return keep


def _uuid_census(res: Any, *, allowed_types: Any) -> dict[str, set]:
    """F-DUPE: the PRE-dedup uuid census, deliberately a second pass over the same accumulator.

    `product_code` is unique PER COMPANY, so one code under two uuids is TWO companies'
    products, not a data-entry duplicate. `_token_candidates` dedupes by code and throws that
    evidence away. `isExact` entries ARE counted here (unlike above): an exact-tier twin
    still proves the code is cross-company ambiguous, and the same product resolved at two
    tiers carries one uuid, which the set collapses.
    """
    acc: list = []
    matches = jsc.get(res, "matches")
    if isinstance(matches, list):
        acc.extend(matches)
    alternatives = jsc.get(res, "alternatives")
    if isinstance(alternatives, list):
        acc.extend(alternatives)
    census: dict[str, set] = {}
    for match in acc:
        code = jsc.get(match, "canonical_code")
        if not jsc.truthy(code):
            continue
        entity_type = jsc.get(match, "entity_type")
        if allowed_types is not None and jsc.truthy(entity_type) and entity_type not in allowed_types:
            continue
        if not _is_uuid(jsc.get(match, "uuid")):
            continue
        census.setdefault(_norm(code), set()).add(jsc.js_string(jsc.get(match, "uuid")))
    return census


def _scoping_from(requires: list, *, gate: Any, resolved: Any) -> list:
    """F3 layer-1 scoping entity, with the picker lane's resolver fallback.

    On the picker lane `compatible_entities` is REPLACED by the option uuids, so the
    attachment-type uuid is not there - fall back to the resolver's own flatten. Additive:
    the other lanes still find it in `compatible_entities` first.
    """
    seen: list = []
    out: list = []
    # ATTACHMENT-MULTI R5: the resolver's `canonical_code` for a document type is
    # `code or type_name`, and twelve dev types carry a slug code (`packing_list`). The stamp
    # names the type, so the human `type_name` rides along - only where it differs from the
    # code, so every type with no code (every product type on dev today) is byte-identical.
    type_name_by_uuid: dict[Any, Any] = {}
    description_by_uuid: dict[Any, Any] = {}
    for match in _flat_matches(resolved):
        display = jsc.get(match, "display")
        uuid = jsc.get(match, "uuid")
        if jsc.truthy(uuid) and jsc.truthy(jsc.get(display, "type_name")):
            type_name_by_uuid.setdefault(uuid, jsc.get(display, "type_name"))
        if jsc.truthy(uuid) and jsc.truthy(jsc.get(display, "description")):
            description_by_uuid.setdefault(uuid, jsc.get(display, "description"))

    def take(entity: Any) -> None:
        entity_type = jsc.nullish_str(jsc.get(entity, "entity_type"))
        uuid = jsc.get(entity, "uuid")
        if entity_type not in requires or not _is_uuid(uuid) or uuid in seen:
            return
        seen.append(uuid)
        code = jsc.get(entity, "code")
        if code is None:
            code = jsc.get(entity, "canonical_code")
        row = {"uuid": uuid, "entity_type": entity_type, "code": code if code is not None else None}
        type_name = type_name_by_uuid.get(uuid)
        if entity_type == "attachment_type" and jsc.truthy(type_name) and type_name != code:
            row["type_name"] = type_name
        out.append(row)

    compatible = jsc.get(gate, "compatible_entities")
    for entity in (compatible if isinstance(compatible, list) else []):
        take(entity)
    if len(out) == 0:
        for match in _flat_matches(resolved):
            take(match)
    # A several-type ask stamps per type, and the MCP presenter prints a type's DESCRIPTION
    # when it has one ("Product Photos, Photo, Image, Pictures by Marketing" on dev), so the
    # annotator needs both spellings to know which row is which type. Only on such an ask,
    # so every single-type plan stays byte-equal.
    if sum(1 for row in out if row["entity_type"] == "attachment_type") > 1:
        for row in out:
            if row["entity_type"] != "attachment_type":
                continue
            if jsc.truthy(type_name_by_uuid.get(row["uuid"])):
                row["type_name"] = type_name_by_uuid[row["uuid"]]
            if jsc.truthy(description_by_uuid.get(row["uuid"])):
                row["description"] = description_by_uuid[row["uuid"]]
    return out


#: PLAN-chatbot-answer-half-reattach.md R4, AC-1710: the did-you-mean print cap's own
#: fallback - a missing kind, or `roster_caps` itself missing/`None` entirely (a caller
#: with no opinion on the column at all). MEASURED (not the R3 `gate.py::_roster_cap`
#: convention of 10): every existing caller of `dym_transform`/`run_miss_lane` - the
#: still-live `complete_answer` path and `test_s6c_answer_lane.py`'s own byte-for-byte
#: n8n replay corpus - passes no `roster_caps` at all and is graded against today's
#: hard `_cap3` (3) print cut; widening the fallback to 10 changed the PRINTED
#: candidate count for every one of those captures (measured: 11 divergences,
#: `dym-transform` and `dym-transform-partial` fixtures). AC-1710's own red test
#: (`test_a_missing_or_none_roster_caps_keeps_a_default`) only asserts `<= 10`, which
#: 3 already satisfies - so the fallback stays 3, byte-identical to `_cap3`, and only a
#: caller with a REAL opinion (the bridge, via the entity kind's own `roster_cap`
#: column) ever prints more.
_DYM_CAP_DEFAULT = 3


def _dym_plan(
    item: dict[str, Any] | None,
    *,
    parser: Any,
    resolved: Any,
    gate: Any,
    partial_lane: bool,
    variant: str,
    roster_caps: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """The did-you-mean PROBE PLANNER, shared by both deployments of the body.

    `variant` is `"full"` (`sub-miss-suggest`'s `dym-transform`) or `"partial"`
    (`sub-answer`'s `dym-transform-partial`). Every difference between the two bodies is
    named at the branch that carries it.

    PASSTHROUGH IS LOAD-BEARING: `dym-gate`'s FALSE branch feeds the composer directly, and
    the composer starts from its input and expects the not-found payload, so this spreads
    its input and APPENDS control keys the composer strips again.
    """
    full = variant == "full"
    _pass = item if isinstance(item, dict) else {}
    q = parser if isinstance(parser, dict) else {}
    r = resolved if isinstance(resolved, dict) else {}
    g = gate if isinstance(gate, dict) else {}

    allowed_lookup = jsc.get(jsc.get(g, "gate_debug"), "allowed_lookup")
    allowed_types = allowed_lookup if isinstance(allowed_lookup, list) else None
    unresolved = jsc.array(jsc.get(r, "unresolved_tokens"))
    is_clar = _pass.get("is_clarification") is True
    require_spec = jsc.get(g, "require_specific") is True

    # Fix 4 hoists `domain` / `cfg` / `_isPartialLane` ABOVE the candidate build on the full
    # body, because `tokenCandidates` and `_capForBlock` both need to know the lane. On the
    # partial body they are computed further down; the VALUES are identical either way.
    gate_domain = jsc.get(jsc.get(g, "gate_debug"), "domain")
    domain = gate_domain if jsc.truthy(gate_domain) else (jsc.get(q, "domain_hint") or None)
    # `promotion` is on the FULL body's table only. The partial body has two entries, so a
    # promotion turn there is `domain_not_enabled` - measured on five `sub-answer-live`
    # captures, which is what a shared table would have got wrong.
    table = DOMAIN_PROBE if full else PARTIAL_DOMAIN_PROBE
    cfg = table.get(domain) if isinstance(domain, str) else None
    # `partial` is excluded from uuid keying because `dym-annotate-partial` is a separate
    # deployed copy that was never updated to read `dym_probe_row_keys` / `probe_uuid_keyed`.
    uuid_keyed_domain = full and domain == "product_attachment" and not partial_lane

    misses = miss_resolutions(r, gate=g)

    d1s: list[dict[str, Any]] = []
    if not is_clar and not require_spec:
        # C1 (full body only): the planner must mirror the composer's own block EXACTLY -
        # cross-token dedupe by normalised code (or uuid when uuid-keyed) FIRST, and NO cap
        # on the number of missed tokens. The partial body still carries the five-token cap
        # and no cross-token dedupe, which is the pre-C1 shape it was promoted with.
        seen_key: list[str] = []
        for res in misses:
            cands = _token_candidates(res, allowed_types=allowed_types, uuid_keyed=uuid_keyed_domain)
            if full:
                kept = []
                for match in cands:
                    key = (
                        jsc.js_string(jsc.get(match, "uuid") or "").strip().lower()
                        if uuid_keyed_domain
                        else jsc.js_string(jsc.get(match, "canonical_code") or "").strip().lower()
                    )
                    if not key or key in seen_key:
                        continue
                    seen_key.append(key)
                    kept.append(match)
                cands = kept
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
                # `_res` is carried for the F-DUPE census only; the composer's own copy of
                # this block does not have it and nothing downstream reads it.
                d1s.append({"token": token, "cands": cands, "_res": res})
        if not full:
            d1s = d1s[:5]

    # F2 (owner ruling): the per-token UX cap is BYPASSED for `product_attachment` on the
    # uuid-keyed lanes, so "every uuid row stamped" has something to render, not just probe.
    #
    # AC-1710 (R4): the print cut point itself honours `roster_caps`, keyed by the
    # block's own candidate kind (`entity_type`) - a missing kind, or `roster_caps`
    # itself missing/`None` (no opinion at all), falls back to `_DYM_CAP_DEFAULT`. This
    # is a DIFFERENT cap from `DOMAIN_PROBE[domain]["probe_cap"]` (how many candidates
    # get PROBED for the has/no annotation, never how many are PRINTED) - the two must
    # never be conflated.
    def cap_for_block(cands: list) -> list:
        if uuid_keyed_domain:
            return list(cands)
        kind = None
        if cands:
            kind = jsc.nullish_str(jsc.get(cands[0], "entity_type")).strip().lower() or None
        cap = _DYM_CAP_DEFAULT
        if isinstance(roster_caps, Mapping):
            configured = roster_caps.get(kind or "")
            if isinstance(configured, int) and configured > 0:
                cap = configured
        return list(cands[:cap])

    survivors: list[dict[str, Any]] = []
    for block in d1s:
        picks = [
            {"m": match, "label": human_label(match)} for match in cap_for_block(block["cands"])
        ]
        picks = [p for p in picks if jsc.truthy(p["label"])]
        if picks:
            survivors.append({"block": block, "picks": picks})

    # Fix 5: `gate.compatible_entities` carries no `company_name` (only the RESOLVER's raw
    # match objects do), and the (code, company) join in `dym-annotate` needs it or every
    # cross-company duplicate code collides onto one composite key.
    match_by_uuid: dict[str, Any] = {}
    if full:
        for match in _flat_matches(r):
            if jsc.truthy(match) and jsc.truthy(jsc.get(match, "uuid")):
                key = jsc.js_string(jsc.get(match, "uuid"))
                if key not in match_by_uuid:
                    match_by_uuid[key] = match

    # PICKER lane: the require-specific numbered picker the gate rendered into
    # `gate_clarification`. Its candidates are the gate's own option set, already
    # uuid-carrying; D1 never fires on these turns, which is why the surface was un-annotated.
    def picker_cands() -> list | None:
        if not cfg:
            return None
        if jsc.get(g, "require_specific") is not True:
            return None
        if not jsc.nullish_str(jsc.get(g, "gate_clarification")).strip():
            return None
        compatible = jsc.get(g, "compatible_entities")
        compatible = compatible if isinstance(compatible, list) else []
        if not compatible:
            return None
        out = []
        for entity in compatible:
            row = {
                "canonical_code": jsc.get(entity, "code"),
                "uuid": jsc.get(entity, "uuid"),
                "entity_type": jsc.get(entity, "entity_type"),
            }
            if full:
                raw_match = match_by_uuid.get(jsc.js_string(jsc.get(entity, "uuid")))
                company = jsc.get(raw_match, "company_name")
                row["company_name"] = company if jsc.truthy(company) else None
            out.append(row)
        return out

    picker = picker_cands()

    probe_needed = False
    probe_skip_reason: Any = None
    probe_tool: Any = None
    probe_noun: Any = None
    probe_predicate: Any = None
    dym_probe_entities: list = []
    dym_candidate_codes: list = []
    dym_candidate_uuids: list = []
    dym_excluded_codes: list = []
    dym_probe_row_keys: list = []
    dym_capped_codes: list = []
    probe_cap_applied = False
    probe_uuid_keyed = False
    probe_lane = "d1"

    picks: list | None = None
    census: dict[str, set] | None = None
    if cfg:
        if picker:
            probe_lane = "picker"
            picks = [{"m": match} for match in picker]
            census = {}
            for match in picker:
                if not jsc.truthy(jsc.get(match, "canonical_code")) or not _is_uuid(
                    jsc.get(match, "uuid")
                ):
                    continue
                census.setdefault(_norm(jsc.get(match, "canonical_code")), set()).add(
                    jsc.js_string(jsc.get(match, "uuid"))
                )
        elif len(survivors) >= 1:
            # C3: multi-token is allowed on the D1 lane too. The objection was to the SORT,
            # never to the suffix, and C3 annotates without re-sorting, so the renumbering
            # hazard does not exist. `probe_skip_reason: 'multi_token'` is now unreachable on
            # both lanes; the literal is RETAINED below so a runData search distinguishes
            # "never fired" from "constant removed".
            probe_lane = "partial" if partial_lane else "d1"
            picks = [pick for s in survivors for pick in s["picks"]]
            census = {}
            for s in survivors:
                for key, uuids in _uuid_census(s["block"]["_res"], allowed_types=allowed_types).items():
                    census.setdefault(key, set()).update(uuids)

    if not cfg:
        probe_skip_reason = "domain_not_enabled"
    elif picks is None:
        probe_skip_reason = "multi_token" if len(survivors) > 1 else "no_d1_candidates"
    elif len(picks) == 0:
        probe_skip_reason = "no_d1_candidates"
    else:
        # Fix 4 / Fix 5: PER-UUID stamping for `product_attachment` on the D1 and picker
        # lanes, never on `partial` (its annotator is a separate copy that reads codes).
        uuid_keyed = full and domain == "product_attachment" and probe_lane in ("d1", "picker")
        probe_uuid_keyed = uuid_keyed

        cands: list = []
        seen: list = []
        dropped_other = 0
        for pick in picks:
            match = pick.get("m") or {}
            code = jsc.get(match, "canonical_code")
            entity_type = jsc.get(match, "entity_type")
            entity_type = entity_type if jsc.truthy(entity_type) else "product"
            if not jsc.truthy(code):
                continue
            key = _norm(code)
            if not uuid_keyed:
                # F-DUPE. More than one uuid behind this code means more than one company's
                # product, the probe answers carry no product id, and the PICK resolves to
                # exactly one uuid - so a union would promise "has" and dead-end on the empty
                # twin. Excluding the code renders it BARE while its unambiguous siblings are
                # still labelled.
                owners = (census or {}).get(key)
                if owners and len(owners) > 1:
                    if not any(_norm(jsc.get(x, "code")) == key for x in dym_excluded_codes):
                        dym_excluded_codes.append(
                            {
                                "code": jsc.js_string(code),
                                "reason": "multi_uuid_code",
                                "uuid_count": len(owners),
                            }
                        )
                    continue
            if not _is_uuid(jsc.get(match, "uuid")):
                dropped_other += 1  # unresolvable: cannot narrow the probe
                continue
            if jsc.js_string(entity_type) not in MAPPABLE:
                dropped_other += 1  # no *_ids filter: would unscope the read
                continue
            dedupe_key = jsc.js_string(jsc.get(match, "uuid")).lower() if uuid_keyed else key
            if dedupe_key in seen:
                continue
            seen.append(dedupe_key)
            cands.append(
                {
                    "uuid": jsc.get(match, "uuid"),
                    "entity_type": jsc.js_string(entity_type),
                    "code": jsc.js_string(code),
                }
            )
            dym_candidate_codes.append(jsc.js_string(code))
            if uuid_keyed:
                dym_candidate_uuids.append(jsc.js_string(jsc.get(match, "uuid")).lower())
                dym_probe_row_keys.append(
                    {
                        "uuid": jsc.js_string(jsc.get(match, "uuid")).lower(),
                        "code": jsc.js_string(code),
                        "company": jsc.nullish_str(jsc.get(match, "company_name")).strip(),
                    }
                )

        # C3 (i): apply the cap. `cands` and `dym_candidate_codes` are built in the same loop
        # under the same condition, so a matched truncation is exact. FAIL-OPEN: a missing,
        # non-positive or non-numeric cap disables the cap rather than dropping candidates.
        cap = jsc.js_number(cfg.get("probe_cap"))
        if (
            isinstance(cap, (int, float))
            and not jsc.is_nan(cap)
            and cap not in (float("inf"), float("-inf"))
            and cap > 0
            and len(cands) > cap
        ):
            cap_int = int(cap)
            dym_capped_codes = [c["code"] for c in cands[cap_int:]]
            del cands[cap_int:]
            dym_candidate_codes = dym_candidate_codes[:cap_int]
            if probe_uuid_keyed:
                dym_candidate_uuids = dym_candidate_uuids[:cap_int]
                dym_probe_row_keys = dym_probe_row_keys[:cap_int]
            probe_cap_applied = True

        if len(cands) == 0:
            # HARD GATE. An empty `product_ids` makes the stock tool return every product x
            # every active warehouse: a huge read and 100% false positives. Name the F-DUPE
            # exclusion when it is the SOLE cause, so runData distinguishes "nothing
            # resolvable" from "everything ambiguous".
            probe_skip_reason = (
                "multi_uuid_code"
                if (len(dym_excluded_codes) > 0 and dropped_other == 0)
                else "no_candidate_uuid"
            )
            dym_candidate_codes = []
            if full:
                dym_candidate_uuids = []
                dym_probe_row_keys = []
        else:
            scoping: list = []
            requires = cfg.get("requires")
            if isinstance(requires, list) and requires:
                scoping = _scoping_from(requires, gate=g, resolved=r)
                if len(scoping) == 0:
                    # The gate can pass on a raw parser HINT with no uuid. Probing without an
                    # attachment-type filter returns every attachment of every type, so a
                    # brochure-only product would be labelled "has certificate". Fail closed.
                    probe_skip_reason = "no_scoping_entity"
                    dym_candidate_codes = []
            if not jsc.truthy(probe_skip_reason):
                probe_needed = True
                probe_tool = cfg["tool"]
                probe_noun = cfg["noun"]
                probe_predicate = cfg["predicate"]
                dym_probe_entities = [*cands, *scoping]

    out: dict[str, Any] = {
        **_pass,
        "dym_probe_entities": dym_probe_entities,
        "dym_candidate_codes": dym_candidate_codes,
    }
    if full:
        out["dym_candidate_uuids"] = dym_candidate_uuids
    out["dym_excluded_codes"] = dym_excluded_codes
    if full:
        out["dym_probe_row_keys"] = dym_probe_row_keys
    out["dym_capped_codes"] = dym_capped_codes
    out["probe_cap_applied"] = probe_cap_applied
    out["probe_tool"] = probe_tool
    out["probe_noun"] = probe_noun
    out["probe_predicate"] = probe_predicate
    if full:
        out["probe_uuid_keyed"] = probe_uuid_keyed
    out["probe_needed"] = probe_needed
    out["probe_skip_reason"] = probe_skip_reason
    out["probe_lane"] = probe_lane
    # Sentinel. `dym-probe` runs `onError: continueRegularOutput`, so a failed sub-call emits
    # THIS item unchanged, and `dym-annotate` keys on the sentinel to tell "probe failed"
    # apart from a real (possibly empty) probe envelope - the not-found payload passed
    # through can itself carry an `answers` key.
    out["_dym_probe_input"] = True
    return out


def dym_transform(
    item: dict[str, Any] | None,
    *,
    parser: Any,
    resolved: Any,
    gate: Any = None,
    central_exchange: Any = None,
    roster_caps: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """`dym-transform` (561 lines), the miss lane's probe planner.

    `gate` is `ctx.gate` off `build-ctx-resolved`; `central_exchange` is the three-state
    `isExecuted` read that tells this ONE body which of its two deployments is running (it
    is never executed inside `sub-miss-suggest`, so the lane is `d1` there).

    `roster_caps` (PLAN-chatbot-answer-half-reattach.md R4, AC-1710) is the printed
    did-you-mean list's own cut point, keyed by entity kind - `chatbot_entity_kinds.
    roster_cap`, the SAME column R3's gate arms read, threaded through here too.
    """
    return _dym_plan(
        item,
        parser=parser,
        resolved=resolved,
        roster_caps=roster_caps,
        gate=gate,
        partial_lane=central_exchange is not None,
        variant="full",
    )


# --------------------------------------------------------------------------- #
# dym-annotate / dym-annotate-partial - again ONE body, two deployments.
# --------------------------------------------------------------------------- #

# C3 mitigation (ii): PAGE SATURATION. The backend default page size is
# `app/schemas/common.py` `limit: int = 50` and nothing in the envelope reports truncation,
# so a full page is the only signal available that rows may have been cut. A saturated page
# means ok:false, zero annotation, byte-identical to the un-annotated offer. It can only
# ever WITHHOLD an annotation, never invent one.
_PAGE_SATURATION = 50

_PRODUCT_CODE_RE = re.compile(r"product\s*code", re.IGNORECASE)
_ATTACHMENT_TYPE_RE = re.compile(r"attachment\s*type", re.IGNORECASE)
_QUANTITY_ON_HAND_RE = re.compile(r"^\s*quantity\s*on\s*hand\s*\Z", re.IGNORECASE)
# #768: the `compact` stock-visibility mode (`sorento_crm_mcp/presenters.py::_stock_compact`)
# renders "Total" + one field per location code, never "Quantity On Hand".
_STOCK_TOTAL_RE = re.compile(r"^\s*total\s*\Z", re.IGNORECASE)
_COMPANY_RE = re.compile(r"^\s*company\s*\Z", re.IGNORECASE)
# `String(raw ?? '').replace(/[^0-9.\-]/g, '')` - ASCII digits only, like JS's own grammar.
_NON_NUMERIC_RE = re.compile(r"[^0-9.\-]")

# The presenter's own em dash for an empty value. Written as a named escape so the repo's
# dash guard cannot mistake a data literal for prose.
_EMPTY_VALUE = "\u2014"


def _code_of(answer: Any) -> str | None:
    """`codeOf`: the title, else the field whose label looks like "Product Code". Verbatim."""
    code = jsc.get(answer, "title") if jsc.truthy(answer) else None
    if not jsc.truthy(code) and jsc.truthy(answer) and isinstance(jsc.get(answer, "fields"), list):
        field = jsc.find(
            jsc.get(answer, "fields"),
            # D11-reproduced: `dym-annotate`'s own `codeOf` label match over the PRESENTER's
            # field labels (never the customer's words).
            lambda x: bool(_PRODUCT_CODE_RE.search(jsc.js_string(jsc.get(x, "label")))),
        )
        code = jsc.get(field, "value") if jsc.truthy(field) else None
    return _norm(code) if jsc.truthy(code) else None


def _field_val(answer: Any, pattern: re.Pattern[str]) -> Any:
    """`fieldVal`: the first field whose LABEL matches, or null."""
    if not jsc.truthy(answer) or not isinstance(jsc.get(answer, "fields"), list):
        return None
    field = jsc.find(
        jsc.get(answer, "fields"),
        # D11-reproduced: `dym-annotate`'s own `fieldVal` label match, presenter labels only.
        lambda x: jsc.truthy(x) and bool(pattern.search(jsc.nullish_str(jsc.get(x, "label")))),
    )
    return jsc.get(field, "value") if jsc.truthy(field) else None


def _annotate(
    probe: Any,
    *,
    payload: Any,
    transform: Any,
    probe_items: Any,
    full: bool,
) -> dict[str, Any]:
    """The annotator both lanes deploy. `full` adds Fix 4's uuid keying and D18's promotion arm.

    FAIL OPEN. Any doubt means ok:false, zero annotation, today's un-annotated offer. Failure
    is detected by PAYLOAD SHAPE, never by node status: an unwired error output makes a broken
    run report success.
    """
    out = dict(payload) if isinstance(payload, dict) else {}
    xf = transform if isinstance(transform, dict) else {}
    probe_json = probe if isinstance(probe, dict) else {}

    uuid_keyed = full and xf.get("probe_uuid_keyed") is True
    probed_source = (
        jsc.get(xf, "dym_candidate_uuids") if uuid_keyed else jsc.get(xf, "dym_candidate_codes")
    )
    meta: dict[str, Any] = {
        "ok": False,
        "tool": xf.get("probe_tool") if jsc.has(xf, "probe_tool") else None,
        "noun": xf.get("probe_noun") if jsc.has(xf, "probe_noun") else None,
        "predicate": xf.get("probe_predicate") if jsc.has(xf, "probe_predicate") else None,
    }
    if full:
        meta["key_mode"] = "uuid" if uuid_keyed else "code"
    meta["probed"] = [_norm(c) for c in jsc.array(probed_source)]
    meta["answer_count"] = 0
    meta["reason"] = None

    available: list = []
    # F1 amendment (owner ruling 2026-09-01): a composite join that cannot attribute an
    # answer row to exactly one twin must never guess. The RENDER forces these codes BARE.
    dym_ambiguous_codes: list = []
    dym_ambiguous_uuids: list = []

    answers = jsc.get(probe_json, "answers")
    if not isinstance(answers, list):
        items = jsc.get(probe_json, "items")
        answers = items if isinstance(items, list) else None

    # FAIL-OPEN DETECTION, in a deliberate order. The SENTINEL is checked FIRST: under an
    # input-passthrough failure the item is `dym-transform`'s output, which is a spread of the
    # not-found payload and can legitimately carry an `answers` key, so shape-sniffing alone
    # would read a FAILED probe as a successful EMPTY one and stamp a confident "no <noun>" on
    # every candidate. Checking the sentinel first makes that misdetection unreachable.
    if jsc.truthy(probe_json) and probe_json.get("_dym_probe_input") is True:
        meta["reason"] = "probe_error"
    elif jsc.truthy(probe_json) and jsc.truthy(probe_json.get("error")):
        meta["reason"] = "probe_error"
    elif answers is None:
        meta["reason"] = "no_answers_array"
    elif len(answers) >= _PAGE_SATURATION:
        # C3 (ii), checked BEFORE either predicate: both attribute by code, and an
        # attribution built on a truncated page is wrong in the one direction that matters.
        meta["answer_count"] = len(answers)
        meta["reason"] = "page_saturated"
    elif meta["predicate"] == "qty_gt_zero":
        meta["answer_count"] = len(answers)
        # #768: the probe's row SHAPE depends on the stock tool's visibility mode - a
        # `detailed` row (`item()`) carries "Quantity On Hand", a `compact` row
        # (`_stock_compact`) carries "Total" plus one field per location, and an
        # `availability` row (`_stock_availability`) carries NO fields at all, only
        # item-level flags. Summing "Quantity On Hand" unconditionally read every
        # non-detailed probe as all-zero. `stock_visibility.mode` names the mode directly;
        # `result_type` is the fallback for a probe envelope that never carried that block.
        # Every fixture captured before #768 carries NEITHER key, so `meta["mode"]` is only
        # stamped when the probe actually signalled one - an unsignalled probe still resolves
        # to "detailed" below, but stays byte-identical to those captures.
        sv_mode = jsc.get(jsc.get(probe_json, "stock_visibility"), "mode")
        result_type = jsc.get(probe_json, "result_type")
        if isinstance(sv_mode, str) and sv_mode:
            mode = sv_mode
        elif isinstance(result_type, str):
            # Guarded: `.get()` on a dict raises for an unhashable key, and this whole
            # function runs OUTSIDE the probe's own try - a hostile, non-string
            # `result_type` must fall to "detailed" rather than fail the turn.
            mode = {"stock_compact": "compact", "stock_availability": "availability"}.get(
                result_type, "detailed"
            )
        else:
            mode = "detailed"
        if (isinstance(sv_mode, str) and sv_mode) or jsc.truthy(result_type):
            meta["mode"] = mode

        if mode == "detailed":
            # One row per product x per ACTIVE WAREHOUSE, and a genuine 0 is still returned, so
            # presence is not has-stock. Sum "Quantity On Hand"; absent / unparseable counts 0.
            sums: dict[str, Any] = {}
            for answer in answers:
                code = _code_of(answer)
                if not code:
                    continue
                raw = _field_val(answer, _QUANTITY_ON_HAND_RE)
                number = jsc.js_number(_NON_NUMERIC_RE.sub("", jsc.nullish_str(raw)))
                addend = number if jsc.is_integer(number) or isinstance(number, float) else 0
                if jsc.is_nan(number) or number in (float("inf"), float("-inf")):
                    addend = 0
                sums[code] = sums.get(code, 0) + addend
            available = [code for code, total in sums.items() if total > 0]
            meta["ok"] = True
        elif mode == "compact":
            # `_stock_compact` rows carry one "Total" field plus one per location code
            # (which `hide_zero_locations` can drop to none) - the per-location fields are
            # never summed, only "Total" drives availability. Rows are per (product,
            # company): a Sorento twin and a Mocha twin of the same code carry NO company
            # field to tell them apart, so their totals ACCUMULATE the same way the
            # detailed arm's `sums` do - overwriting let a zero-stock twin erase a
            # stocked one.
            totals: dict[str, Any] = {}
            for answer in answers:
                code = _code_of(answer)
                if not code:
                    continue
                raw = _field_val(answer, _STOCK_TOTAL_RE)
                number = jsc.js_number(_NON_NUMERIC_RE.sub("", jsc.nullish_str(raw)))
                total = number if jsc.is_integer(number) or isinstance(number, float) else 0
                if jsc.is_nan(number) or number in (float("inf"), float("-inf")):
                    total = 0
                totals[code] = totals.get(code, 0) + total
            available = [code for code, total in totals.items() if total > 0]
            meta["ok"] = True
        elif mode == "availability":
            # #768 ruling: this arm always fails open, and is written to say why rather
            # than to read the row. The DYM probe seam never sends `requested_qty`
            # (`entity_ids_transformer` in fetch.py emits no such key), so the backend
            # answers `needs_quantity: True, available: None` on every row today. And even
            # if `requested_qty` ever arrived, `available` is a QUANTITY VERDICT
            # (`on_hand >= requested_qty`), not "has stock details" - trusting a False
            # would render a confident "no" on a code that has SOME stock, just less than
            # the (irrelevant, did-you-mean-context) requested amount.
            meta["reason"] = "availability_unknown"
        else:
            meta["reason"] = "unknown_stock_mode"
    elif full and meta["predicate"] == "row_present":
        # D18: the PER-CANDIDATE lane (promotion). Promotion rows carry no product, so
        # attribution is POSITIONAL - input item i is candidate i. That is an ordering
        # assumption, so it is CHECKED, never trusted: disagreeing counts mean ok stays false
        # and nobody is told "no promotion" about a code that has them.
        items = [
            (x.get("json") or {}) if isinstance(x, dict) and "json" in x else (x or {})
            for x in (probe_items if isinstance(probe_items, list) else [probe_json])
        ]
        codes = meta["probed"]
        if not codes or len(items) != len(codes):
            meta["reason"] = "per_candidate_pairing_mismatch"
        elif any(jsc.truthy(j) and jsc.get(j, "_dym_probe_input") is True for j in items):
            meta["reason"] = "probe_error"  # the sentinel, applied to EVERY item on this lane
        else:
            meta["answer_count"] = sum(len(jsc.array(jsc.get(j, "answers"))) for j in items)
            # Generous on purpose, and the direction matters: a false "no" hides a suggestion
            # that would have worked; a false "has" costs the customer one pick.
            available = [
                code
                for index, code in enumerate(codes)
                if (
                    jsc.get(items[index] if index < len(items) else {}, "has_result") is True
                    or len(jsc.array(jsc.get(items[index] if index < len(items) else {}, "answers"))) > 0
                )
            ]
            meta["ok"] = True
    elif meta["predicate"] == "row_present_with_type":
        meta["answer_count"] = len(answers)
        # Defence in depth: a probe that lost its `attachment_type_ids` narrowing returns
        # EVERY attachment of every type, which would label a brochure-only product "has
        # certificate". Rows with no Attachment Type do not count, and an all-untyped
        # non-empty answer set is treated as unscoped, annotating nothing.
        has: list = []
        typed = 0

        # Fix 4: join each answer row back to the ONE candidate uuid it belongs to via
        # (code, company). The presenter's row carries a "Company" field beside the code and
        # never a product uuid, which is the reason F-DUPE existed at all.
        row_keys = (
            jsc.get(xf, "dym_probe_row_keys")
            if uuid_keyed and isinstance(jsc.get(xf, "dym_probe_row_keys"), list)
            else []
        )
        by_composite: dict[str, Any] = {}
        by_code: dict[str, list] = {}
        for row_key in row_keys:
            code = _norm(jsc.get(row_key, "code"))
            uuid = _norm(jsc.get(row_key, "uuid"))
            if not code or not uuid:
                continue
            composite_key = f"{code}|{_norm(jsc.get(row_key, 'company'))}"
            # C2-a: two candidate twins can share the same composite key. Last-write-wins
            # would silently pick one uuid and let an answer row attribute to it as if
            # unambiguous, which is exactly the false-"no" hazard the join exists to prevent.
            # Store null instead; the lookup then treats it as a miss and falls through to the
            # ambiguity fallback.
            by_composite[composite_key] = None if composite_key in by_composite else uuid
            by_code.setdefault(code, []).append(uuid)

        ambiguous_codes: list = []
        ambiguous_uuids: list = []
        # ATTACHMENT-MULTI R2: with two or more asked types "has" is per (product, type) - a
        # photo-only product must not read "has Technical Specifications". One list per
        # asked type, in ask order; a row whose type is none of them counts for none.
        asked_types = _asked_types(xf) if (full and uuid_keyed) else []
        has_by_type: list[list] = [[] for _ in asked_types]

        def _credit(owner: Any, type_value: str) -> None:
            if owner not in has:
                has.append(owner)
            type_key = _norm(type_value)
            for index, asked in enumerate(asked_types):
                if type_key in asked["keys"] and owner not in has_by_type[index]:
                    has_by_type[index].append(owner)

        for answer in answers:
            type_value = jsc.nullish_str(_field_val(answer, _ATTACHMENT_TYPE_RE)).strip()
            if type_value == "" or type_value == _EMPTY_VALUE:
                continue
            typed += 1
            code = _code_of(answer)
            if not code:
                continue
            if not uuid_keyed:
                if code not in has:
                    has.append(code)
                continue
            company = _norm(_field_val(answer, _COMPANY_RE))
            composite = by_composite.get(f"{code}|{company}")
            if jsc.truthy(composite):
                _credit(composite, type_value)
                continue
            # No company match. Exactly ONE owner means nothing to disambiguate; more than
            # one and the code's identity is UNKNOWN from this row, so it is marked ambiguous
            # rather than guessing every owner "has" (F1). The render goes BARE.
            owners = by_code.get(code) or []
            if len(owners) == 1:
                _credit(owners[0], type_value)
                continue
            if len(owners) > 1:
                if code not in ambiguous_codes:
                    ambiguous_codes.append(code)
                for owner in owners:
                    if owner not in ambiguous_uuids:
                        ambiguous_uuids.append(owner)
        if len(answers) > 0 and typed == 0:
            meta["reason"] = "unscoped_probe"
        else:
            available = list(has)
            dym_ambiguous_codes = list(ambiguous_codes)
            dym_ambiguous_uuids = list(ambiguous_uuids)
            meta["ok"] = True
            if len(asked_types) > 1:
                # Emitted ONLY for a several-type ask, so every single-type capture of this
                # node stays byte-equal; `build_suggest_offer` strips it with `_DYM_CTRL_KEYS`.
                out["dym_has_by_type"] = [
                    {"type": asked["name"], "has": has_by_type[index]}
                    for index, asked in enumerate(asked_types)
                ]
    else:
        meta["reason"] = "unknown_predicate"

    if not meta["ok"]:
        available = []
        dym_ambiguous_codes = []
        dym_ambiguous_uuids = []

    out["dym_available_codes"] = available
    out["dym_probe_meta"] = meta
    if full:
        out["dym_ambiguous_codes"] = dym_ambiguous_codes  # [] on every non-uuid-keyed turn
        out["dym_ambiguous_uuids"] = dym_ambiguous_uuids  # F8's uuid companion, likewise
    # #750, decision 1: on the uuid-keyed lane `probed` / `dym_available_codes` are UUIDs and
    # every render keys by the CODE it printed, so the composer needs the planner's own
    # (uuid, code, company) map to translate. Carried HERE rather than re-derived there: this
    # node already holds it, and a second derivation would be a second thing to keep in step
    # with `_dym_plan`.
    if uuid_keyed:
        out["dym_probe_row_keys"] = jsc.array(jsc.get(xf, "dym_probe_row_keys"))
    # #750, decision 3: the noun the customer reads is the RESOLVED attachment type
    # ("Product Photos"), not their own word for it ("photo", "gambar"). The scoping entity is
    # what the probe was actually filtered on, so it is the only honest name for what was
    # looked for. `DOMAIN_PROBE["product_attachment"].noun` is None precisely because the type
    # is not known until the turn resolves one; the domains that DO carry a literal noun
    # (`inventory`, `promotion`) declare no `requires` and so never reach this.
    #
    # BOTH keys are `full`-only. The partial lane's annotator is a separate deployed copy
    # whose reader keys by code and takes its
    # noun from the parser, so a key it never reads would move that node's contract for
    # nothing. On the FULL lane the type name IS emitted for a code-keyed turn too (it says
    # what the probe was scoped to, which has nothing to do with the key mode), and that is
    # why five `dym-annotate` captures are registered in `tests/chatbot/divergences.py`.
    # `build_suggest_offer` strips both again with the rest of `_DYM_CTRL_KEYS`, so its own
    # emitted object is unchanged either way.
    if full:
        type_name = _scoping_type_name(xf)
        if type_name:
            out["dym_probe_type_name"] = type_name
    return out


_TYPE_SCOPES: frozenset[str] = frozenset({"attachment_type", "certificate"})


def _scoping_entities(transform: Any) -> list:
    """The plan's trailing run of type-scoped entities - the scoping `_dym_plan` appends
    after the candidates (`[*cands, *scoping]`), in the order the gate kept them."""
    run: list = []
    for entity in reversed(jsc.array(jsc.get(transform, "dym_probe_entities"))):
        if _norm(jsc.get(entity, "entity_type")) not in _TYPE_SCOPES:
            break
        run.append(entity)
    run.reverse()
    return run


def _asked_types(transform: Any) -> list[dict[str, Any]]:
    """`[{name, keys}]` per asked attachment type, or `[]` when the probe was scoped by
    anything other than plain attachment types (a certificate keeps its family stamp).

    `name` is the human `type_name` (R5), never a slug code; `keys` are the spellings an
    answer row's "Attachment Type" field can carry for it - the presenter prints the
    description when the type has one, else the name.
    """
    out: list[dict[str, Any]] = []
    for entity in _scoping_entities(transform):
        if _norm(jsc.get(entity, "entity_type")) != "attachment_type":
            return []
        code = jsc.nullish_str(jsc.get(entity, "code")).strip()
        name = jsc.nullish_str(jsc.get(entity, "type_name")).strip() or code
        if not name:
            return []
        description = jsc.get(entity, "description")
        out.append({"name": name, "keys": {k for k in (_norm(name), _norm(code), _norm(description)) if k}})
    return out


def _scoping_type_name(transform: Any) -> str:
    """The document type the probe was scoped to, off the plan's own `dym_probe_entities`.

    REVERSE order, and that is load-bearing: `_dym_plan` builds the list as
    `[*cands, *scoping]`, and a CANDIDATE can itself be type-shaped (`attachment_type` and
    `certificate` are both in `MAPPABLE` and in the domain's `ALLOWED` set, so a misspelt
    document token can arrive as a did-you-mean candidate). Scanning forwards would name that
    candidate; scanning backwards reads the scoping entities, which are appended last and are
    the only entities the probe was actually filtered on. The list is empty whenever scoping
    is empty (`_dym_plan` fails closed on `no_scoping_entity` before it is built), so the
    reverse scan cannot fall through to a candidate on a turn that HAS scoping.

    A `certificate` entity's `canonical_code` is a certificate NUMBER
    (`entity_resolver.py` ~1701), never a type name, so it names its family instead of
    stamping "- has MS1234-5" at the customer.
    """
    for entity in reversed(jsc.array(jsc.get(transform, "dym_probe_entities"))):
        entity_type = _norm(jsc.get(entity, "entity_type"))
        if entity_type not in _TYPE_SCOPES:
            continue
        if entity_type == "certificate":
            return "certificate"
        code = jsc.nullish_str(jsc.get(entity, "type_name")).strip() or jsc.nullish_str(
            jsc.get(entity, "code")
        ).strip()
        if code:
            return code
    return ""


def dym_annotate(
    item: dict[str, Any] | None,
    *,
    payload: Any = None,
    transform: Any = None,
    probe_items: Any = None,
) -> dict[str, Any]:
    """`dym-annotate` (247 lines): which offered codes actually HAVE the thing.

    `item` is the PROBE envelope (`$input`). `payload` is `not-found-error-message`'s output,
    which this node re-sources BY NAME because the composer starts from its input and would
    otherwise lose `escalate_message` / `is_clarification` / `found_summary` - it is a
    parameter here for the same reason every other by-name read is. `transform` is
    `dym-transform`'s plan; absent, the plan keys are read off the probe item itself, which is
    the shape the sentinel branch genuinely carries (`dym-transform`'s output IS the item n8n
    passes through when the probe sub-call fails). `probe_items` is the full input LIST, which
    only D18's per-candidate promotion lane reads.
    """
    return _annotate(
        item,
        payload=payload,
        transform=transform if transform is not None else item,
        probe_items=probe_items,
        full=True,
    )


# --------------------------------------------------------------------------- #
# promo-dym-plan / sibling-transform - the two fan-out nodes.
# --------------------------------------------------------------------------- #


def promo_dym_plan(transform: dict[str, Any] | None) -> list[dict[str, Any]]:
    """`promo-dym-plan` (D18): one item per candidate, paired BY CODE, never by index.

    `dym_probe_entities` and `dym_candidate_codes` are built by separate loops in
    `dym-transform`, so an index assumption between two independently built arrays is the kind
    that holds until someone filters one of them.

    Never returns an empty list: that would skip every downstream node including the
    annotator, and the composer would render its un-annotated offer with no signal that the
    probe was attempted.
    """
    xf = transform if isinstance(transform, dict) else {}
    codes = jsc.array(jsc.get(xf, "dym_candidate_codes"))
    entities = jsc.array(jsc.get(xf, "dym_probe_entities"))

    by_code: dict[str, Any] = {}
    for entity in entities:
        code = (
            jsc.get(entity, "canonical_code")
            or jsc.get(entity, "code")
            or jsc.get(entity, "raw")
        )
        key = _norm(code)
        if key and key not in by_code:
            by_code[key] = entity

    items: list[dict[str, Any]] = []
    for code in codes:
        entity = by_code.get(_norm(code))
        if entity is None:
            continue  # no entity to scope with means it cannot be probed honestly
        items.append({**xf, "probe_code": code, "probe_entity": entity})
    if not items:
        return [{**xf, "probe_code": None, "probe_entity": None, "probe_plan_empty": True}]
    return items


_BOUNDARY = frozenset({"-", "/", " "})


def sibling_transform(response: Any, *, gate: Any) -> dict[str, Any]:
    """`sibling-transform`: a strict prefix / boundary family filter over the products read.

    Emits ONE item carrying the sibling family so `sibling-probe` runs exactly once. PHASE-2
    seam: the union across multiple base codes builds here.
    """
    g = gate if isinstance(gate, dict) else {}
    compatible = jsc.get(g, "compatible_entities")
    compatible = compatible if isinstance(compatible, list) else []
    base_entity = jsc.find(
        compatible,
        lambda e: jsc.truthy(e)
        and jsc.js_string(jsc.get(e, "entity_type")).lower() == "product"
        and jsc.truthy(jsc.get(e, "code"))
        and not _is_uuid(jsc.get(e, "code")),
    )
    base_code = jsc.js_string(jsc.get(base_entity, "code")) if jsc.truthy(base_entity) else ""
    base_n = _norm(base_code)

    resp = response if response is not None else {}
    rows: list = []
    if isinstance(resp, list):
        rows = resp
    else:
        for key in ("data", "items", "products", "results"):
            value = jsc.get(resp, key)
            if isinstance(value, list):
                rows = value
                break
        else:
            nested = jsc.get(jsc.get(resp, "data"), "items")
            rows = nested if isinstance(nested, list) else []

    def in_family(code: Any) -> bool:
        c = _norm(code)
        if not c or not base_n:
            return False
        if c == base_n:
            return True
        if not c.startswith(base_n):
            return False
        nxt = c[len(base_n) : len(base_n) + 1]
        return nxt == "" or nxt in _BOUNDARY

    seen: list[str] = []
    siblings: list[dict[str, Any]] = []
    for row in rows:
        code = jsc.get(row, "product_code")
        if code is None:
            code = jsc.get(row, "code")
        uuid = jsc.get(row, "id")
        if uuid is None:
            uuid = jsc.get(row, "uuid")
        if not jsc.truthy(code) or not in_family(code):
            continue
        key = _norm(code)
        if key in seen:
            continue
        seen.append(key)
        siblings.append(
            {
                "uuid": uuid if jsc.truthy(uuid) else None,
                "entity_type": "product",
                "code": jsc.js_string(code),
            }
        )
    return {
        "siblings": siblings,
        "base_codes": [base_code] if jsc.truthy(base_code) else [],
        "sibling_count": len(siblings),
    }


# --------------------------------------------------------------------------- #
# miss-suggest-result - the sub's exit.
# --------------------------------------------------------------------------- #


def miss_suggest_result(
    item: dict[str, Any] | None,
    *,
    dym_annotate: Any = None,
    sibling_transform: Any = None,
    sibling_probe: Any = None,
) -> dict[str, Any]:
    """The sub's ONE exit, three mutually exclusive arms.

    A NAMED `isExecuted` check on the convergence, never a positional `$input` guess: n8n's
    fan-out order is not stable, so which arm fired has to be asked, not inferred.

    `outcome_fragment` carries THREE keys, not one. `dym-annotate` is the out-of-lane
    reader a literal sweep cannot see (a dynamic `$(n)` in `build-outcome`'s own map);
    `sibling-transform` / `sibling-probe` are three literal by-name reads inside
    `build-suggest-offer` that the original sweep's "zero out-of-lane readers" claim missed
    outright.
    """
    fragment = {
        "dym-annotate": dym_annotate if dym_annotate is not None else None,
        "sibling-transform": sibling_transform if sibling_transform is not None else None,
        "sibling-probe": sibling_probe if sibling_probe is not None else None,
    }
    if dym_annotate is not None:
        return {**dym_annotate, "outcome_fragment": fragment}
    return {**(item if isinstance(item, dict) else {}), "outcome_fragment": fragment}


# --------------------------------------------------------------------------- #
# The lane itself: `sibling-gate` / `dym-gate` / `if-promo-dym`, in process.
# --------------------------------------------------------------------------- #


def _sibling_gate(*, gate: Any, build_result: Any) -> bool:
    """`sibling-gate`, four AND conditions, verbatim from the IF node's own expressions.

    incoming domain, not require-specific, at least one non-uuid product code in scope, and
    the fetch genuinely came back empty.
    """
    g = gate if isinstance(gate, dict) else {}
    if jsc.get(jsc.get(g, "gate_debug"), "domain") != "incoming":
        return False
    if jsc.get(g, "require_specific") is True:
        return False
    compatible = jsc.get(g, "compatible_entities")
    compatible = compatible if isinstance(compatible, list) else []
    has_product = any(
        jsc.truthy(e)
        and jsc.js_string(jsc.get(e, "entity_type")).lower() == "product"
        and jsc.truthy(jsc.get(e, "code"))
        and not _is_uuid(jsc.get(e, "code"))
        for e in compatible
    )
    if not has_product:
        return False
    if build_result is None:
        return False
    return jsc.get(build_result, "has_result") is False


def run_miss_lane(
    not_found_item: dict[str, Any] | None,
    *,
    parser: Any,
    resolved: Any,
    gate: Any,
    services: Any,
    build_result: Any = None,
    contact_id: Any = None,
    space_id: Any = None,
    execution_id: Any = None,
    dry_run: bool = False,
    roster_caps: Mapping[str, int] | None = None,
    profile: Any = None,
) -> dict[str, Any]:
    """`sub-miss-suggest` end to end, from `not-found-error-message`'s payload to the exit.

    The graph, and the gates are the graph's own, not a looser in-process approximation:

        not-found-error-message -> sibling-gate
          TRUE  -> family-fetch -> sibling-transform -> sibling-probe -> exit
          FALSE -> dym-transform -> dym-gate
                     TRUE  -> if-promo-dym
                                TRUE  -> promo-dym-plan -> promo-dym-probe -> dym-annotate
                                FALSE -> dym-probe -> dym-annotate
                     FALSE -> exit

    The composer at the end is `build-suggest-offer`, which lives on the SPINE (the RS-7
    errata: the live sub has no such node and the spine reads this lane's `outcome_fragment`
    one hop later). It is called from here anyway, because AC-607 words the miss LANE as
    ending in the offer and because the alternative is the same three-argument fragment
    unpacking written twice; the NODE still lives in `answer.py`, where its own captures
    grade it.

    D14: `dry_run` suppresses WRITES, and there are none on this lane - every seam here is a
    READ, so a dry run makes exactly the same calls a live turn makes. The parameter is
    accepted so the caller does not have to know that.

    `profile` (#1262 slice 11 review round, 26 Sep 2026) forwards straight to
    `build_suggest_offer`'s own audience gate - a did-you-mean/sibling roster's own
    "or would you like me to escalate to X team?" clause must not print for staff, the
    same reason `not_found_error_message` already takes `profile`.
    """
    from app.services.chatbot.lanes.business.answer import build_suggest_offer

    def _compose(exit_item: dict[str, Any]) -> dict[str, Any]:
        fragment = exit_item.get("outcome_fragment") or {}
        return build_suggest_offer(
            exit_item,
            parser=parser,
            resolved=resolved,
            gate=gate,
            dym_annotate=fragment.get("dym-annotate"),
            sibling_probe=fragment.get("sibling-probe"),
            sibling_transform=fragment.get("sibling-transform"),
            execution_id=execution_id,
            profile=profile,
        )

    payload = not_found_item if isinstance(not_found_item, dict) else {}

    if _sibling_gate(gate=gate, build_result=build_result):
        # family-fetch: the products read, by the base product code the gate put in scope.
        compatible = jsc.array(jsc.get(gate, "compatible_entities"))
        base = jsc.find(
            compatible,
            lambda e: jsc.truthy(e)
            and jsc.js_string(jsc.get(e, "entity_type")).lower() == "product"
            and jsc.truthy(jsc.get(e, "code"))
            and not _is_uuid(jsc.get(e, "code")),
        )
        query = jsc.js_string(jsc.get(base, "code")) if jsc.truthy(base) else ""
        family = services.family_fetch(query)
        transformed = sibling_transform(family, gate=gate)
        try:
            probe = services.mcp_probe(
                "crm_incoming_stock_list",
                _probe_args(
                    transformed.get("siblings") or [],
                    parser=parser,
                    contact_id=contact_id,
                    space_id=space_id,
                ),
            )
        except Exception:  # noqa: BLE001 - review round (26 Sep 2026), SF2: this call
            # carried no `onError` (a faithful n8n port, per this function's docstring
            # below), which was fine while a failed tool call returned a string - now
            # that `call_tool` genuinely RAISES (#1262 slice 1), an unwrapped probe here
            # took the whole turn down instead of degrading to the bare did-you-mean
            # offer the sibling `dym-probe` arm already falls back to.
            logger.warning("chatbot: sibling incoming-stock probe did not run", exc_info=True)
            probe = {"error": "probe failed"}
        return _compose(
            miss_suggest_result(payload, sibling_transform=transformed, sibling_probe=probe)
        )

    plan = dym_transform(payload, parser=parser, resolved=resolved, gate=gate, roster_caps=roster_caps)
    if plan.get("probe_needed") is not True:
        return _compose(miss_suggest_result(plan))

    if plan.get("probe_predicate") == "row_present":
        # `if-promo-dym` TRUE: one call per candidate (`mode: each`).
        items = promo_dym_plan(plan)
        results = []
        for row in items:
            try:
                results.append(
                    services.mcp_probe(
                        plan.get("probe_tool"),
                        _probe_args(
                            [row.get("probe_entity")] if row.get("probe_entity") is not None else [],
                            parser=parser,
                            contact_id=contact_id,
                            space_id=space_id,
                        ),
                    )
                )
            except Exception:  # noqa: BLE001 - review round (26 Sep 2026), SF2: same
                # reasoning as the sibling probe above - unwrapped only while a failed
                # tool call returned a string, not now that `call_tool` raises. One
                # candidate's own probe failing degrades that candidate to the bare
                # did-you-mean shape, not the whole turn.
                logger.warning("chatbot: promo did-you-mean probe did not run", exc_info=True)
                results.append({"error": "probe failed"})
        annotated = dym_annotate(
            results[0] if results else {},
            payload=payload,
            transform=plan,
            probe_items=results,
        )
        return _compose(miss_suggest_result(plan, dym_annotate=annotated))

    try:
        probe = services.mcp_probe(
            plan.get("probe_tool"),
            _probe_args(
                plan.get("dym_probe_entities") or [],
                parser=parser,
                contact_id=contact_id,
                space_id=space_id,
            ),
        )
    except Exception:  # noqa: BLE001 - `dym-probe` carries onError: continueRegularOutput
        # The ONLY node in `sub-miss-suggest-live` (and the same node on the spine) with an
        # `onError`, and it is `continueRegularOutput`: a failed probe still emits an item,
        # `dym-annotate` runs on it, and the customer gets the BARE did-you-mean offer.
        # `sibling-probe` and `promo-dym-probe` above now degrade the SAME way (review
        # round, 26 Sep 2026, SF2) - the n8n port's own `onError` shape no longer applies
        # literally now that `call_tool` raises instead of returning an error string, and
        # a probe failure must never take the whole turn down. `family-fetch` is not an
        # MCP tool call at all (no probe to fail the same way), so it stays unwrapped.
        logger.warning("chatbot: did-you-mean probe did not run", exc_info=True)
        probe = {"error": "probe failed"}
    annotated = dym_annotate(probe, payload=payload, transform=plan)
    return _compose(miss_suggest_result(plan, dym_annotate=annotated))


def _probe_args(
    entities: Any, *, parser: Any, contact_id: Any, space_id: Any
) -> dict[str, Any]:
    """`dym-probe` / `sibling-probe` / `promo-dym-probe`'s `workflowInputs`, one shape.

    The three probe nodes carry byte-identical `semantic_input` expressions; only `entities`
    and `tool` differ. `space_id` comes from the workspace row (D5) through the ONE fallback
    every tool call site uses (`fetch.space_id_or_default`), so an install with no workspace
    row still sends n8n's literal here rather than `null` on this path and the literal on
    the fetch's.
    """
    from app.services.chatbot.lanes.business.fetch import space_id_or_default

    q = parser if isinstance(parser, dict) else {}
    return {
        "contact_id": contact_id,
        "entities": entities,
        "semantic_input": {
            "message_type": q.get("message_type") if jsc.has(q, "message_type") else None,
            "intent_hint": q.get("intent_hint") if jsc.has(q, "intent_hint") else None,
            "domain_hint": q.get("domain_hint") if jsc.has(q, "domain_hint") else None,
            "user_goal": q.get("user_goal") if jsc.has(q, "user_goal") else None,
            "access_levels": jsc.array(q.get("access_levels")),
            "contact_id": None if contact_id is None else jsc.js_string(contact_id),
            "space_id": space_id_or_default(space_id),
            "date_mode": q.get("date_mode") if jsc.has(q, "date_mode") else None,
            "date_filter_start": q.get("date_filter_start") if jsc.has(q, "date_filter_start") else None,
            "date_filter_end": q.get("date_filter_end") if jsc.has(q, "date_filter_end") else None,
            "is_active": q.get("is_active") if jsc.has(q, "is_active") else None,
            "order_status": q.get("order_status") if jsc.has(q, "order_status") else None,
            "requested_attributes": jsc.array(q.get("requested_attributes")),
        },
    }
