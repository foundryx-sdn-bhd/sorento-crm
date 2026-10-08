"""Port of `tier-gate.js` (sub-resolve-and-gate, 230 lines).

Sits between `Aggregate` (the contact's entitlement names) and `If4` on the promotion
lane and decides whether the tier is RESOLVABLE - stated by the customer, or the contact
is entitled to exactly one. It is ALSO the only place the tier x brand -> compound
recomposition happens: `access_levels_recomposed` is what the fetch step sends to the
CRM, and `disallowed-entity-gate`'s Q23 held-check reads `tier_stated`, `entitled_tiers`,
`brand_gate_empty`, `brand_unheld` and `query_brands` straight off this output.

The `mapper-embed` block in the JS is a byte-copy of the n8n repo's own
`tests/offline/access-tier/mapper.js` (49/49 probe, 13/13 mutations). It is ported here
as `parse_level` / `map_entitlement` / `recompose` / `needs_tier_ask` with the same names
so a reader can put the two side by side; nothing about it is re-derived.

The three by-name reads the body makes are parameters instead, because the port has no
`$('node')` to resolve: `j` is `Aggregate`'s item, `parser` is `ctx.parse.output`, and
`item` is the sub's own item carrier (which holds the RS-9 `tier_pick*` stamps that
`Aggregate`'s `{name}`-only output strips).
"""
from __future__ import annotations

import re
from typing import Any

from app.services.chatbot import jsc

BRANDS = ("sorento", "cabana", "mocha")


def _tier_order() -> tuple[str, ...]:
    """The tier order (was the module-level `TIER_ORDER`, AC-1594/S6): `chatbot_domains`
    carries no tier axis, so this reads `system_settings.chatbot_tier_order` through
    `turn.policy.default_policy()`'s frozen seed - the same fallback every pure reader
    with no live per-turn `Policy` reaching it uses. Not memoized here: `default_policy`
    itself is."""
    from app.services.chatbot.turn.policy import default_policy

    return default_policy().tier_order

# `\Z`, not `$`: Python's `$` also matches before a trailing newline and JavaScript's does
# not, so a level name ending in "\n" would parse here and not in n8n.
_COMPOUND_RE = re.compile(r"^(sorento|cabana|mocha) (dealer|office)\Z")
_WHITESPACE_RUN = re.compile(r"\s+")


def parse_level(name: Any) -> dict[str, Any] | None:
    """`parseLevel` - "Sorento Dealer" -> `{brand: 'sorento', tier: 'dealer'}`, else None."""
    s = _WHITESPACE_RUN.sub(" ", jsc.js_string("" if name is None else name).strip().lower())
    if s in ("end user", "enduser", "end-user"):
        return {"brand": None, "tier": "end_user"}
    match = _COMPOUND_RE.match(s)
    return {"brand": match.group(1), "tier": match.group(2)} if match else None


def map_entitlement(names: Any) -> dict[str, Any]:
    """`mapEntitlement` - the entitlement names split into brands, tiers and unknowns."""
    brands: set[str] = set()
    tiers: set[str] = set()
    unknown: list[str] = []
    for name in jsc.array(names):
        parsed = parse_level(name)
        if parsed is None:
            if jsc.js_string("" if name is None else name).strip():
                unknown.append(jsc.js_string(name))
            continue
        if parsed["brand"]:
            brands.add(parsed["brand"])
        tiers.add(parsed["tier"])
    return {
        "brands": [b for b in BRANDS if b in brands],
        "tiers": [t for t in _tier_order() if t in tiers],
        "unknown": unknown,
    }


def recompose(tiers: Any, query_brands: Any, entitled: Any) -> dict[str, Any]:
    """`recompose` - the compound access-level names the CRM understands today."""
    want = {
        jsc.js_string("" if t is None else t).strip().lower() for t in jsc.array(tiers)
    }
    qb = [
        b
        for b in (
            jsc.js_string("" if x is None else x).strip().lower()
            for x in jsc.array(query_brands)
        )
        if b in BRANDS
    ]
    ent = jsc.array(entitled)
    ent_map = map_entitlement(ent)
    # "unheld" is only a meaningful claim when the contact HAS brand-scoped entitlement to
    # compare against (R10/R11): no brand axis => nothing to deny.
    brand_unheld_raw = (
        len(qb) > 0
        and len(ent_map["brands"]) > 0
        and not any(b in ent_map["brands"] for b in qb)
    )
    # unheld => allow NO brand-scoped name (probe R5). Falling back to the full
    # entitlement would answer a Cabana ask with Sorento files.
    if qb:
        allow_brands = [] if brand_unheld_raw else [b for b in qb if b in ent_map["brands"]]
    else:
        allow_brands = ent_map["brands"]
    out: list[Any] = []
    for name in ent:
        parsed = parse_level(name)
        if parsed is None or parsed["tier"] not in want:
            continue
        # end_user is brandless TODAY: include it whenever its tier was chosen.
        if parsed["brand"] and parsed["brand"] not in allow_brands:
            continue
        if name not in out:
            out.append(name)
    # F5: a surviving BRANDLESS level covers every brand, the named one included, so
    # claiming they lack it is simply false. `brand_unheld` drives a NOTICE, and a notice
    # is a factual claim - only make it when nothing we are about to send covers the brand.
    served_brandless = any(
        (parsed := parse_level(n)) is not None and not parsed["brand"] for n in out
    )
    return {
        "access_levels": out,
        "brand_gate_empty": brand_unheld_raw and len(out) == 0,
        "brand_unheld": brand_unheld_raw and not served_brandless,
    }


def needs_tier_ask(
    domain: Any,
    stated: Any,
    entitled_tiers: Any,
    opts: dict[str, Any] | None = None,
) -> bool:
    """`needsTierAsk` - ask the tier question, or proceed on what we already know."""
    opts = opts or {}
    # RS-9 Fix 6 round 2 (F23): an out-of-range tier-menu pick must reach the reprompt
    # even on a bare digit that states no domain, and even when the parser's own
    # `_pending_pick` says a pick was resolved - that describes a PROMO-ROW pick.
    force_ask = opts.get("tierPickInvalid") is True
    if not force_ask:
        if domain != "promotion":
            return False
        if opts.get("pendingPick") is True:
            return False
    if opts.get("brandGateEmpty") is True:
        return False
    if isinstance(stated, list) and len(stated) > 0:
        return False
    return len(jsc.array(entitled_tiers)) > 1


def probe_plan(tiers: Any, query_brands: Any, names: Any) -> list[dict[str, Any]]:
    """One probe per entitled tier, its access levels from the same `recompose` the answer
    lane uses, so the availability probe asks exactly what the customer's pick would ask."""
    return [{"tier": t, "access_levels": recompose([t], query_brands, names)["access_levels"]} for t in tiers]


def as_tier_ask(gate: dict[str, Any]) -> dict[str, Any]:
    """`gate` (this function's own output) turned into the ask it would be with no tier
    stated: the plan's own tier question (`turn/apply.py`) is asked over every held tier."""
    return {
        **gate,
        "tier_stated": [],
        "tier_ask": True,
        "tier_proceed": False,
        "tier_probe_plan": probe_plan(gate.get("entitled_tiers") or [], gate.get("query_brands") or [], gate.get("name") or []),
    }


def tier_gate(
    aggregate_item: dict[str, Any],
    *,
    parser: dict[str, Any] | None,
    item: dict[str, Any] | None,
) -> dict[str, Any]:
    """`tier-gate`'s output item: `{...j, tier_stated, ..., tier_pick_domain}`."""
    j = aggregate_item or {}
    names = jsc.array(j.get("name"))
    parser = parser if isinstance(parser, dict) else {}

    # RS-9 Fix 6: `tier_pick` / `tier_pick_invalid` / `tier_pick_domain` are stamped on the
    # item UPSTREAM of this sub, before `Aggregate`'s `{name}`-only output strips every
    # other field. The item carrier still holds them verbatim.
    tier_item = item if isinstance(item, dict) else None
    tier_pick = (
        tier_item.get("tier_pick")
        if tier_item is not None and isinstance(tier_item.get("tier_pick"), str)
        else None
    )
    tier_pick_invalid = bool(
        tier_item is not None and tier_item.get("tier_pick_invalid") is True
    )
    # Fix 8: forwarded verbatim - the fetch step's rag caller falls back to it when the
    # parser stated no domain.
    tier_pick_domain = (
        tier_item.get("tier_pick_domain")
        if tier_item is not None and isinstance(tier_item.get("tier_pick_domain"), str)
        else None
    )

    tier_order = _tier_order()
    stated_set: set[str] = set()
    for a in jsc.array(parser.get("access_levels")):
        s = jsc.js_string("" if a is None else a).strip().lower()
        if s in tier_order:
            stated_set.add(s)
            continue
        parsed = parse_level(a)
        if parsed:
            stated_set.add(parsed["tier"])
    # A valid `tier_pick` OVERRIDES whatever the parser carried this turn; an out-of-range
    # pick forces `tier_stated` EMPTY so `needs_tier_ask` re-asks instead of proceeding on
    # a stale tier.
    if tier_pick_invalid:
        tier_stated: list[str] = []
    elif tier_pick and tier_pick in tier_order:
        tier_stated = [tier_pick]
    else:
        tier_stated = [t for t in tier_order if t in stated_set]

    ent_map = map_entitlement(names)

    # D9: the parser derives `query_brands` now, because only it still has the RAW LLM
    # levels. The entity-only scan is the fallback, so an older parser degrades to
    # yesterday's behaviour instead of throwing.
    if isinstance(parser.get("query_brands"), list):
        query_brands = [
            b
            for b in parser["query_brands"]
            if jsc.js_string("" if b is None else b).lower() in BRANDS
        ]
    else:
        seen: list[str] = []
        for e in jsc.array(parser.get("entities")):
            if jsc.js_string(jsc.get(e, "hint") or "").lower() != "brand":
                continue
            raw = jsc.get(e, "canonical_code") or jsc.get(e, "raw") or ""
            haystack = jsc.js_string("" if raw is None else raw).lower()
            found = next((v for v in BRANDS if v in haystack), None)
            if found and found not in seen:
                seen.append(found)
        query_brands = seen

    # D11: a pick the parser ALREADY resolved outranks the ask. A DYM pick is an
    # EXEMPTION, not a non-contribution - the parser stamps `_pending_pick` on the dym
    # pick turn itself, so only `dym_pick_applied !== true` up front keeps the ask alive.
    pending_pick = (
        parser.get("_tier_pick_scope_reused") is not True
        and parser.get("dym_pick_applied") is not True
        and (
            parser.get("_pending_pick") is True
            or parser.get("_promo_pick_scope_reused") is True
            or parser.get("member_pick_context") is True
        )
    )

    # ORDER MATTERS (F6): recomposition runs BEFORE the ask trigger, so a closed brand
    # gate can CANCEL the ask. On the ask turn `tier_stated` is empty, so `_chosen` is the
    # whole entitled set - exactly the right probe for "does this contact hold the named
    # brand at ANY tier".
    chosen = tier_stated if tier_stated else ent_map["tiers"]
    brand_gate_empty = False
    brand_unheld = False
    if len(chosen) == 0:
        # Entitlement holds no mappable tier (unknown names only): legacy full passthrough.
        access_levels_recomposed = sorted(list(names), key=jsc.js_string)
    else:
        r = recompose(chosen, query_brands, names)
        brand_gate_empty = r["brand_gate_empty"]
        # F1: the NOTICE signal, separate from the SUPPRESSION signal. Only
        # `brand_gate_empty` may suppress.
        brand_unheld = r["brand_unheld"]
        access_levels_recomposed = r["access_levels"]
        if len(access_levels_recomposed) == 0 and not brand_gate_empty and tier_stated:
            # Q23: a stated tier the contact does not hold - answer at their REAL
            # entitlement and let the gate render the notice that explains it.
            access_levels_recomposed = sorted(list(names), key=jsc.js_string)

    # D2 ask trigger. F6: `brandGateEmpty` makes the DENIAL outrank the ask.
    tier_ask = needs_tier_ask(
        parser.get("domain_hint"),
        tier_stated,
        ent_map["tiers"],
        {
            "pendingPick": pending_pick,
            "brandGateEmpty": brand_gate_empty,
            "tierPickInvalid": tier_pick_invalid,
        },
    )
    tier_proceed = len(names) > 0 and not tier_ask

    # D14: the per-tier probe plan, from the SAME `recompose` the answer lane uses, so the
    # availability probe asks exactly the question the customer's pick would ask. Empty
    # off the ask lane, so every other turn is byte-inert.
    tier_probe_plan = probe_plan(ent_map["tiers"], query_brands, names) if tier_ask else []

    return {
        **j,
        "tier_stated": tier_stated,
        "entitled_tiers": ent_map["tiers"],
        "entitled_unknown": ent_map["unknown"],
        "query_brands": query_brands,
        "pending_pick": pending_pick,
        "tier_ask": tier_ask,
        "tier_proceed": tier_proceed,
        "access_levels_recomposed": access_levels_recomposed,
        "tier_probe_plan": tier_probe_plan,
        "brand_gate_empty": brand_gate_empty,
        "brand_unheld": brand_unheld,
        "tier_pick_domain": tier_pick_domain,
    }
