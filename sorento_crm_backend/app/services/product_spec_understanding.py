"""Understand what a customer asked for, using the same vocabulary the ranker scores.

This is the *semantic* half of spec search. The ranker (`product_spec_search`) stays
deterministic and explainable on purpose - every point it awards can be pointed at. What
it could never do is understand language: it matched registry synonyms as literal
substrings, so one dropped letter ("inteligent") resolved nothing, and any phrasing not
already written into the synonym list was invisible.

Fuzzy string distance was the cheap answer and it is the wrong one. It buys tolerance for
typos and nothing else: it cannot know that "for my kitchen" means a kitchen sink, that
"the toilet where the pipe goes into the wall" is a P-trap, or that "not too deep"
qualifies a dimension. Those need a model that understands language, which is what this
module adds.

**The model never invents vocabulary.** It is handed the registry - the same rows the
ranker weights and the n8n parser reads - and every key and value it returns is validated
back against those rows. Anything unrecognised is dropped, not coerced. So the model
decides *meaning*; the registry still decides *what exists*. That boundary is what keeps
a hallucinated `mounting=levitating` out of a customer's shortlist.

Degrades to the deterministic resolver on any failure - no key, no provider, bad JSON,
timeout. Search that got worse when the LLM had a bad day would be a worse product than
search that never used one.

Ticket: jayson-odoo/sorento-crm#98.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

# The env-key half of the ladder is read inside `resolve_api_key`, off this same
# singleton - re-exported here so a test can empty it (`understanding.settings`)
# and be certain no real provider call can escape, whichever provider the agent
# resolves to. Not referenced in this module by design; do not prune it.
from app.config import settings  # noqa: F401
from app.models.ai_assistant import AIAssistantUsageLog
from app.models.product import Brand
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services.ai_prompt_registry import agent_model, get_prompt
from app.services.llm_provider import get_provider, resolve_api_key, resolve_model
# The keys a product may hold more than one of. Imported rather than restated: the set
# is derivation's, and a second copy would let an accepted proposal store a shape a
# re-derivation of the same words would not.
from app.services.product_spec_derivation import MULTI_VALUE_KEYS
from app.services.product_spec_registry import (
    active_registry,
    merged_allowed_values,
    merged_synonyms,
)
from app.services.product_spec_search import (
    BRAND_KEY,
    NEGATOR_WORDS,
    SELF_SYNONYM_KEY,
    normalise_quantity,
    resolve_terms_to_specs_with_spans,
)

logger = logging.getLogger(__name__)

# Words that turn the thing after them into a refusal, in the two languages the
# catalogue's customers write in. Word-boundary matched, so "notch" and "nonstick"
# are not refusals. The vocabulary itself is owned by `product_spec_search`, which
# also keeps these words out of `unrecognized_terms`: a word we read as a refusal is
# never a word we admit we did not understand. Longest first so "non" is preferred
# over "no" when both could start a match.
_NEGATORS = re.compile(
    r"(?<!\w)("
    + "|".join(sorted(NEGATOR_WORDS, key=lambda word: (-len(word), word)))
    + r")(?!\w)"
)

# How far before a value's own words a negator may sit and still refuse it. Wide
# enough for "kitchen sink, not the glass one", short enough that a "not" about
# one thing does not reach across the sentence and refuse the next.
_NEGATION_WINDOW = 15

# Understanding one short sentence. Kept small and cheap: this runs on a customer's
# message, so it is on the latency path of a WhatsApp reply.
_MAX_OUTPUT_TOKENS = 700
_TEMPERATURE = 0.0

# The prompt lives in the AI prompt registry under the agent name below, so it is
# editable and versioned in the UI alongside every other agent's. The hardcoded copy
# (its registry fallback) is what runs if the registry row is missing.
AGENT_NAME = "spec_understanding"

# The sibling agent: reading a statement ABOUT one product (a flyer card, a supplier
# blurb) rather than a customer's enquiry. Its own prompt key because the two jobs pull
# in opposite directions - understanding a customer means interpreting them, and reading
# a supplier's copy means refusing to.
EXTRACTOR_AGENT_NAME = "spec_extractor"

# Longer than the enquiry budget: one flyer card can state a dozen specifications, and
# this runs behind a button on a staff screen rather than on a WhatsApp reply.
_EXTRACT_MAX_OUTPUT_TOKENS = 1500


@dataclass
class Understanding:
    """What the customer meant, in the ranker's own terms."""

    specs: list[dict] = field(default_factory=list)
    # What the customer RULED OUT. Kept apart from `specs` because a refusal is not a
    # request for the opposite: "not glass" removes glass, it does not ask for ceramic.
    exclusions: list[dict] = field(default_factory=list)
    free_terms: list[str] = field(default_factory=list)
    # The customer's OWN words that earned each binding, verbatim, keyed by the
    # spec key they earned. A caller deciding "was this part of the sentence
    # answered" needs the words that were heard, not the key they resolved to:
    # "double bowl" earns `bowl_count=2`, and nobody reading `bowl_count` back
    # can tell whether "double" was understood. Keyed rather than flat because
    # binding a word is not the same as ANSWERING it - only the caller knows
    # which keys the products it is about to show actually matched.
    bound_phrases: dict[str, list[str]] = field(default_factory=dict)
    notes: str = ""
    # How this was produced, so the preview screen can be honest about it and a
    # reviewer can tell a model result from a fallback.
    source: str = "deterministic"
    model: str | None = None
    elapsed_ms: int | None = None


@dataclass
class Extraction:
    """What a pasted piece of text says about one product, as the registry's own terms.

    `specs` are `{key, value, evidence}` and are already validated: a key or a value the
    registry does not recognise never reaches this object. `source` is `semantic` only
    when a model actually answered - anything else (no key, no provider, bad JSON, a
    timeout) degrades to `deterministic`, where the caller's rule pass stands alone.
    """

    specs: list[dict] = field(default_factory=list)
    source: str = "deterministic"
    model: str | None = None


# An open-vocabulary key has no closed list in the registry because its values come from
# the catalog and grow with it (`class`, `brand`). Sourcing them at prompt time is the
# only way the model can recognise one: told merely that `brand` is an enum with no
# values, it cannot know "sorento" is a brand, and correctly answers that the word maps
# to nothing. Capped because a prompt is not a catalog dump.
_OPEN_VOCABULARY_LIMIT = 60


def open_vocabulary_values(db: Session, spec_key: str) -> list[str]:
    """Distinct stored values for a key - simply what the catalog holds.

    Read from the derived specs rather than from `product_categories` so it stays true
    for any open key, not only the two that happen to come from a category today.

    Deliberately NOT cached in the process. A DISTINCT over JSONB across 22,805 rows
    costs ~160ms per key, which is tempting to memoise, but a cache keyed on the spec
    key alone is wrong the moment more than one database is involved - it would serve
    one database's brands to another's queries, silently. Against the 1.5-2.5s model
    call this sits behind, the query is not the thing worth optimising, and a wrong
    vocabulary is not worth 300ms.
    """
    rows = (
        db.query(ProductSpecifications.values[spec_key]["value"].astext)
        .filter(ProductSpecifications.values.has_key(spec_key))  # noqa: W601 - JSONB op
        .distinct()
        .limit(_OPEN_VOCABULARY_LIMIT)
        .all()
    )
    return sorted({value for (value,) in rows if value})


def _offerable(values, excluded: set[str]) -> list[str]:
    """The values a customer may ask for: everything the catalog holds, minus the
    placeholders that record the absence of one."""
    return [v for v in values if str(v).strip().lower() not in excluded]


def _vocabulary(db: Session) -> tuple[list[dict], dict[str, Any], dict[str, list[str]]]:
    """The registry rendered for a prompt, an index to validate replies against, and
    the catalog-sourced values for any open-vocabulary key."""
    described: list[dict] = []
    index: dict[str, Any] = {}
    open_values: dict[str, list[str]] = {}

    for row in active_registry(db):
        synonyms = merged_synonyms(row)
        excluded = {str(v).strip().lower() for v in (row.excluded_values or [])}
        entry: dict[str, Any] = {
            "spec_key": row.spec_key,
            "means": row.label,
            "type": row.data_type,
        }
        if row.unit:
            entry["unit"] = row.unit
        # Merged: a value staff added must be offerable, or the model can never return
        # the thing they just told the system exists.
        shipped_and_added = merged_allowed_values(row)
        if shipped_and_added:
            entry["allowed_values"] = _offerable(shipped_and_added, excluded)
        elif row.data_type == "enum":
            # Open vocabulary: hand the model what the catalog actually holds, minus
            # anything marked not-searchable. A value the model is never shown is a
            # value it cannot answer with, which is a harder guarantee than a rule.
            catalog_values = _offerable(open_vocabulary_values(db, row.spec_key), excluded)
            if catalog_values:
                entry["allowed_values"] = catalog_values
                open_values[row.spec_key] = catalog_values
        # Synonyms are what a customer might say. `_self` names the measurement itself
        # ("thickness", "trap"), the rest name a value ("wall hung", "double bowl").
        customer_words = sorted(
            {w for value, words in synonyms.items() for w in words if value != SELF_SYNONYM_KEY}
        )
        if customer_words:
            entry["customers_say"] = customer_words[:40]
        if synonyms.get(SELF_SYNONYM_KEY):
            entry["called"] = list(synonyms[SELF_SYNONYM_KEY])
        if row.applies_when:
            entry["only_for"] = row.applies_when

        described.append(entry)
        index[row.spec_key] = row

    # The brand is the product's own field, not a specification (#1286, D1), but a
    # customer names one and the model has to be able to say so. So it is offered as a
    # synthetic entry over the Brands master: the names customers can ask for
    # (`is_searchable`), and the placeholders (OTHERS, NO LOGO) held back as excluded so
    # `_coerce` refuses them if the model produces one anyway. A transient row, never
    # added to the session.
    brands = _brand_vocabulary(db)
    if brands is not None:
        described.append(
            {
                "spec_key": BRAND_KEY,
                "means": brands.label,
                "type": brands.data_type,
                # Capped like any open vocabulary: a prompt is not a catalogue dump.
                # The index keeps every name, so a reply naming one still validates.
                "allowed_values": list(brands.allowed_values)[:_OPEN_VOCABULARY_LIMIT],
            }
        )
        index[BRAND_KEY] = brands
        open_values[BRAND_KEY] = list(brands.allowed_values)

    return described, index, open_values


def _brand_vocabulary(db: Session) -> ProductSpecRegistry | None:
    """The Brands master as a transient `brand` vocabulary row, or None when empty."""
    from sqlalchemy import func

    from app.models.product import Product
    from app.services.product_spec_search import UNBINDABLE_BRAND_NAMES

    searchable: list[str] = []
    placeholders: list[str] = []
    # Active brands only, the ones products carry most first (review N-6): the prompt
    # takes the first `_OPEN_VOCABULARY_LIMIT`, and cutting alphabetically dropped real
    # brands for ones nobody sells.
    rows = (
        db.query(Brand.brand_name, Brand.is_searchable, func.count(Product.id))
        .outerjoin(Product, Product.brand_id == Brand.id)
        .filter(Brand.is_active.is_(True))
        .group_by(Brand.id, Brand.brand_name, Brand.is_searchable)
        .order_by(func.count(Product.id).desc(), Brand.brand_name)
        .all()
    )
    for name, is_searchable, _products in rows:
        name = str(name or "").strip()
        if not name:
            continue
        # The floor under the flag (review S-2): OTHERS is never offered.
        offered_here = is_searchable and name.lower() not in UNBINDABLE_BRAND_NAMES
        bucket = searchable if offered_here else placeholders
        if name not in bucket:
            bucket.append(name)
    if not searchable:
        return None
    # One name, one answer (security review N1): an all-companies caller can see one
    # company's OTHERS as a placeholder and another's as a real brand. Offered wins, so
    # a name is never both offered and held back.
    offered = {name.lower() for name in searchable}
    placeholders = [name for name in placeholders if name.lower() not in offered]
    return ProductSpecRegistry(
        spec_key=BRAND_KEY,
        label="Brand",
        data_type="enum",
        allowed_values=searchable,
        excluded_values=sorted(placeholders),
        synonyms={},
    )


def _coerce(row, value: Any, open_values: list[str] | None = None) -> Any | None:
    """Force a model's value into the shape the catalog stores, or reject it.

    Rejection is the important half. A value the registry does not recognise is
    dropped rather than passed through, so the model can be wrong without the wrongness
    reaching a customer.
    """
    data_type = (row.data_type or "").lower()

    if isinstance(value, (list, tuple)) and row.spec_key in MULTI_VALUE_KEYS:
        # "Rose Gold + Matt Black" is one tap in two finishes, and both readings are
        # true. `apply_rules` already produces the list and derivation stores it, so
        # coercing the list element-wise and KEEPING its shape is what makes an
        # accepted proposal store what a re-derivation of the same words would have.
        # Stringified, the whole list matches nothing in the vocabulary and the reading
        # is dropped on the floor - the text said something real and no screen shows it.
        items: list = []
        for item in value:
            coerced = _coerce(row, item, open_values)
            if coerced is not None and coerced not in items:
                items.append(coerced)
        if not items:
            return None
        # One tone is stored as the tone, exactly as `apply_rules` does it.
        return items[0] if len(items) == 1 else items

    if data_type == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "yes", "1"}

    if data_type == "numeric":
        # The model was told the key's unit, but customers volunteer their own and
        # models echo them. Re-normalise defensively.
        if isinstance(value, (int, float)):
            return float(value)
        converted = normalise_quantity(str(value))
        return converted

    text = str(value).strip()
    if not text:
        return None

    # Belt as well as braces: the model is not offered an excluded value, and is not
    # allowed to return one it inferred from elsewhere in the prompt either.
    excluded = {str(v).strip().lower() for v in (row.excluded_values or [])}
    if text.lower() in excluded:
        logger.info("spec understanding: dropped excluded value %r for %s", value, row.spec_key)
        return None

    allowed = _offerable(merged_allowed_values(row) or list(open_values or []), excluded)
    if not allowed:
        # Nothing to check against - the key is open and the catalog is empty for it.
        return text

    # Enum slugs use underscores ("wall_hung") but catalog-sourced values keep their own
    # spacing and casing ("American Standard"), so both readings are tried and the
    # CATALOG's spelling is what gets returned.
    variants = {text.lower(), text.lower().replace(" ", "_"), text.lower().replace("_", " ")}
    for candidate in allowed:
        if str(candidate).lower() in variants:
            return candidate

    # Give the model's phrasing one chance against the synonym table before dropping it.
    for candidate, words in merged_synonyms(row).items():
        if candidate == SELF_SYNONYM_KEY:
            continue
        if any(str(w).lower() == text.lower() for w in words):
            return candidate

    logger.info("spec understanding: dropped unknown value %r for %s", value, row.spec_key)
    return None


def _validated_pairs(
    items: Any,
    index: dict[str, Any],
    open_values: dict[str, list[str]] | None,
    *,
    one_per_key: bool,
) -> list[dict]:
    """Model-proposed {key, value} pairs, kept only where the registry recognises both."""
    out: list[dict] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        row = index.get(key)
        if row is None:
            logger.info("spec understanding: dropped unknown key %r", key)
            continue
        if one_per_key and key in seen:
            continue
        value = _coerce(row, item.get("value"), (open_values or {}).get(key))
        if value is None:
            continue
        entry = {"key": key, "value": value}
        # A comparison, where the customer made one. Only ever attached to a number:
        # "at least chrome" is not a thing, and an operator on an enum would be read by
        # the ranker as a threshold against a string.
        op = str(item.get("op") or "").strip().lower()
        if op in {"at_least", "at_most"} and isinstance(value, (int, float)):
            entry["op"] = op
        out.append(entry)
        seen.add(key)
    return out


def _validate(
    payload: dict, index: dict[str, Any], open_values: dict[str, list[str]] | None = None
) -> tuple[list[dict], list[dict], list[str], str]:
    specs = _validated_pairs(payload.get("specs"), index, open_values, one_per_key=True)
    # A customer can rule out several values of one key ("not glass, not plastic"), so
    # exclusions are NOT collapsed to one per key the way requests are.
    exclusions = _validated_pairs(
        payload.get("exclusions"), index, open_values, one_per_key=False
    )
    # A value cannot be both asked for and refused. When the model says both, the
    # refusal is the more specific statement and the request is dropped - the failure
    # this guards is "not glass" arriving as material=glass, which is the exact
    # opposite of what was said.
    refused = {(e["key"], str(e["value"]).strip().lower()) for e in exclusions}
    specs = [s for s in specs if (s["key"], str(s["value"]).strip().lower()) not in refused]

    free_terms = [str(t).strip() for t in (payload.get("free_terms") or []) if str(t).strip()]
    notes = str(payload.get("notes") or "").strip()
    return specs, exclusions, free_terms, notes


def _resolve_provider(db: Session, agent_name: str = AGENT_NAME):
    """The provider and model THIS agent runs on. None when unconfigured.

    Per-agent, falling back to the global assistant config. Reading a misspelt customer
    sentence is not the same job as writing an explanatory paragraph, so the model is
    set against the agent's own row rather than shared with everything.

    `agent_name` is an argument rather than the module constant because two agents call
    this: the enquiry reader and the extractor beneath it. Hardcoded, the extractor
    read its own words (its system prompt IS asked for under `spec_extractor`) with
    whatever model an admin had configured for the enquiry reader, and nothing on
    either screen said so.

    The agent's provider is operator-settable, so it is often NOT the one the
    assistant row is configured for; the key therefore comes from the shared
    `resolve_api_key`, which only hands over the generic key column when it
    belongs to the provider being asked for. Reading that column unconditionally
    posted the OpenAI key to Google whenever this agent was pointed at Gemini.
    """
    from app.models.ai_assistant import AIAssistantConfig

    cfg = (
        db.query(AIAssistantConfig)
        .order_by(AIAssistantConfig.created_at.asc())
        .first()
    )
    agent_provider, agent_model_name = agent_model(db, agent_name)
    provider_name = agent_provider or (cfg.provider if cfg else "openai") or "openai"
    model_name = resolve_model(cfg, provider_name, agent_model_name)
    api_key = resolve_api_key(cfg, provider_name)
    if not api_key:
        return None, provider_name, model_name

    try:
        return get_provider(provider_name, api_key, model=model_name), provider_name, model_name
    except ValueError:
        logger.warning("spec understanding: unknown provider %r", provider_name)
        return None, provider_name, model_name


def _split_refusals(
    specs: list[dict],
    spans: dict[str, list[tuple[int, int]]],
    haystack: str,
) -> tuple[list[dict], list[dict]]:
    """Move every spec the customer said "not" in front of into the refusals.

    The word-level reader has no concept of negation: it sees "kitchen sink, not
    glass" as the word "glass" and offers glass sinks first, which is the exact
    opposite of what was asked. Only the model could hear the refusal, so a
    customer without the LLM flag on was answered backwards.

    Nothing here is inferred. A binding is refused only when a negator word ends
    within `_NEGATION_WINDOW` characters before the span that EARNED the binding,
    so "glass kitchen sink" is untouched and a "not" about one value cannot
    silently refuse another.

    Spans consumed by a brand phrase are not read as negators: "no logo" is the
    catalogue's own name for the unbranded range, and reading its "no" as a
    refusal would have turned a real ask into a refusal of the kitchen sink
    beside it (F8 is why the brand binds at all).
    """
    if not specs or not haystack:
        return specs, []

    brand_spans = spans.get("brand") or []
    negators = [
        match
        for match in _NEGATORS.finditer(haystack)
        if not any(start <= match.start() and match.end() <= end for start, end in brand_spans)
    ]
    if not negators:
        return specs, []

    kept: list[dict] = []
    refused: list[dict] = []
    for entry in specs:
        own_spans = spans.get(str(entry.get("key"))) or []
        negated = any(
            0 <= start - match.end() <= _NEGATION_WINDOW
            for (start, _end) in own_spans
            for match in negators
        )
        (refused if negated else kept).append(entry)
    return kept, refused


def derive_search_inputs(
    db: Session,
    phrase: str | None,
    *,
    specs: list[dict] | None = None,
    free_terms: list[str] | None = None,
    allow_model: bool = True,
    user_id: str | None = None,
    registry_rows=None,
    log_usage: bool = True,
) -> tuple[list[dict], list[str], list[dict], Understanding | None]:
    """Read the sentence, then let the caller's own extraction win over it.

    The ONE place a raw customer sentence becomes ranker inputs. Both surfaces
    that do this - the resolve endpoint n8n calls and the Product Specifications
    preview page - used to carry their own copy of the same six lines, which is
    how the two readings drifted apart in the first place (the preview handled
    raw text; resolve hid the same call behind an LLM flag). One helper, so a
    fix to how a sentence is read reaches both callers or neither.

    Returns `(specs, free_terms, exclusions, understanding)`. `understanding` is
    None when there was no phrase to read.
    """
    merged_specs = list(specs or [])
    merged_terms = list(free_terms or [])
    if not phrase or not phrase.strip():
        return merged_specs, merged_terms, [], None

    understanding = understand_phrase(
        db,
        phrase,
        user_id=user_id,
        allow_model=allow_model,
        registry_rows=registry_rows,
        log_usage=log_usage,
    )
    # A spec the caller pinned by hand always wins: they saw the whole sentence
    # (or the screen), this saw one phrase.
    stated = {str(entry.get("key")) for entry in merged_specs if entry.get("key")}
    merged_specs = merged_specs + [
        entry for entry in understanding.specs if entry["key"] not in stated
    ]
    merged_terms = merged_terms + [
        term for term in understanding.free_terms if term not in merged_terms
    ]
    return merged_specs, merged_terms, list(understanding.exclusions), understanding


def understand_phrase(
    db: Session,
    phrase: str,
    *,
    user_id: str | None = None,
    allow_model: bool = True,
    registry_rows=None,
    log_usage: bool = True,
) -> Understanding:
    """Map a customer's sentence onto registry specs, semantically where possible.

    Always returns something usable. The deterministic resolver is both the fallback
    and the floor: whatever the model finds is merged ON TOP of it, so a literal
    synonym match can never be lost by asking a model.

    `log_usage=False` skips the `ai_assistant_usage_logs` row and NOTHING else. It exists
    for D14: a chatbot turn marked `is_test` / `test_run_id` must write ZERO rows outside
    `chatbot.turns`, and this was the one write the business lane could still reach
    through the resolve endpoint. The model is still CALLED and the reading is unchanged,
    which is the point - a dry run has to answer the same as a live one or the shadow
    comparison it exists for would be comparing two different behaviours.
    """
    phrase = (phrase or "").strip()
    if not phrase:
        return Understanding()

    # The deterministic reading first - it is free, and it is the floor.
    baseline, spans, haystack = resolve_terms_to_specs_with_spans(
        db, [phrase], registry_rows=registry_rows
    )
    # ...and it now hears a refusal too, so the floor cannot reinstate the thing
    # the customer ruled out (see _split_refusals).
    baseline, baseline_refusals = _split_refusals(baseline, spans, haystack)
    # Every span that bound something, refusals included: "not glass" understood
    # as a refusal is still the word "glass" heard.
    bound_phrases = {
        str(entry.get("key")): [
            haystack[start:end] for (start, end) in spans.get(str(entry.get("key"))) or []
        ]
        for entry in baseline + baseline_refusals
        if spans.get(str(entry.get("key")))
    }
    fallback = Understanding(
        specs=baseline,
        exclusions=baseline_refusals,
        free_terms=[phrase],
        bound_phrases=bound_phrases,
        source="deterministic",
    )

    if not allow_model:
        return fallback

    provider, provider_name, model_name = _resolve_provider(db)
    if provider is None:
        return fallback

    described, index, open_values = _vocabulary(db)
    messages = [
        {"role": "system", "content": get_prompt(db, AGENT_NAME).text},
        {
            "role": "user",
            "content": (
                "Specifications that exist:\n"
                + json.dumps(described, ensure_ascii=False)
                + "\n\nCustomer said:\n"
                + phrase
            ),
        },
    ]

    started = time.monotonic()
    try:
        result = provider.chat(
            messages,
            temperature=_TEMPERATURE,
            max_tokens=_MAX_OUTPUT_TOKENS,
            response_format={"type": "json_object"},
        )
        payload = json.loads(result.content or "{}")
    except Exception as exc:  # noqa: BLE001 - any provider failure degrades, never raises
        logger.warning("spec understanding failed, using deterministic reading: %s", exc)
        return fallback

    elapsed_ms = int((time.monotonic() - started) * 1000)
    specs, exclusions, free_terms, notes = _validate(
        payload if isinstance(payload, dict) else {}, index, open_values
    )

    # A refusal the word-level reader heard stands even when the model missed it,
    # for the same reason the literal spec reading survives: a refusal understood
    # is not something a model's silence should undo.
    already_refused = {(e["key"], str(e["value"]).strip().lower()) for e in exclusions}
    exclusions = exclusions + [
        entry
        for entry in baseline_refusals
        if (entry["key"], str(entry["value"]).strip().lower()) not in already_refused
    ]

    # The model supplements the literal reading, it does not replace it. If a synonym
    # matched outright, that is not something a model should be able to talk us out of.
    merged = list(specs)
    stated = {entry["key"] for entry in merged}
    merged.extend(entry for entry in baseline if entry["key"] not in stated)

    # ...with one exception: the literal reader has no idea what "not" means, so it
    # answers "not glass" with material=glass. A refusal the model DID understand
    # overrules it, or the deterministic floor reinstates the very thing refused.
    refused = {(e["key"], str(e["value"]).strip().lower()) for e in exclusions}
    merged = [s for s in merged if (s["key"], str(s["value"]).strip().lower()) not in refused]

    # The whole phrase always stays available as free text: it is what the rendered
    # sentence is matched against, and dropping it would lose recall the model cannot
    # replace.
    terms = [phrase] + [t for t in free_terms if t.lower() != phrase.lower()]

    # D14: `log_usage=False` skips the bookkeeping row and nothing else. Guarded HERE, at
    # the write, rather than at the caller, so the model is still asked and `merged` /
    # `terms` / `notes` below are computed identically - a dry run has to READ the sentence
    # the same way a live turn does, or the shadow comparison it exists for would be
    # comparing two different behaviours.
    if log_usage:
        try:
            db.add(
                AIAssistantUsageLog(
                    user_id=user_id,
                    feature="spec_search",
                    provider=provider_name,
                    model=model_name,
                    prompt_tokens=int(result.prompt_tokens or 0),
                    completion_tokens=int(result.completion_tokens or 0),
                    total_tokens=int(result.total_tokens or 0),
                    cached_tokens=int(getattr(result, "cached_tokens", 0) or 0),
                    tool_calls_count=0,
                    response_time_ms=elapsed_ms,
                    was_answered=bool(merged),
                )
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001 - never fail a search over bookkeeping
            logger.warning("spec understanding: usage log failed: %s", exc)
            db.rollback()

    return Understanding(
        specs=merged,
        exclusions=exclusions,
        free_terms=terms,
        # The literal reader's spans still hold: the model adds meaning on top of
        # them, it does not unsay the words that matched outright.
        bound_phrases=bound_phrases,
        notes=notes,
        source="semantic",
        model=model_name,
        elapsed_ms=elapsed_ms,
    )


def extract_specs_from_text(
    db: Session,
    text: str,
    *,
    vocabulary: tuple[list[dict], dict[str, Any], dict[str, list[str]]] | None = None,
    allow_model: bool = True,
    user_id: str | None = None,
) -> Extraction:
    """Read a statement about ONE product onto the registry. Writes no product data.

    The sibling entry point AC-B.4 asks for: it shares `_vocabulary`, `_coerce` and
    `_validated_pairs` with `understand_phrase`, so neither can propose vocabulary the
    ranker has never heard of, and `understand_phrase` is NOT overloaded with a mode
    flag - it carries a WhatsApp latency budget this does not.

    Degrades rather than fails, exactly as the enquiry reader does: no provider, a
    provider that raises, or a reply that is not JSON all return an empty
    `Extraction` marked `deterministic`, and the caller's rule pass is what the user
    gets (AC-B.5). `vocabulary` is accepted so a caller that already rendered the
    registry does not pay for it twice - the open-vocabulary values behind it are a
    DISTINCT per key.

    The ONE thing it does write is an `AIAssistantUsageLog` row, and only when a model
    actually answered: the same bookkeeping `understand_phrase` writes for
    `spec_search`, under `feature="spec_extract"`, so a model call made from the
    product page is billed and counted beside every other one. Best effort, exactly as
    there - a failed insert is rolled back and warned about, never raised, because
    losing the reading over its own bookkeeping would be the worse outcome. Nothing
    about the product's specifications is touched (AC-B.1).
    """
    text = (text or "").strip()
    if not text or not allow_model:
        return Extraction()

    provider, provider_name, model_name = _resolve_provider(db, EXTRACTOR_AGENT_NAME)
    if provider is None:
        return Extraction()

    described, index, open_values = vocabulary if vocabulary is not None else _vocabulary(db)
    messages = [
        {"role": "system", "content": get_prompt(db, EXTRACTOR_AGENT_NAME).text},
        {
            "role": "user",
            "content": (
                "Specifications that exist:\n"
                + json.dumps(described, ensure_ascii=False)
                + "\n\nText about the product:\n"
                + text
            ),
        },
    ]

    started = time.monotonic()
    try:
        result = provider.chat(
            messages,
            temperature=_TEMPERATURE,
            max_tokens=_EXTRACT_MAX_OUTPUT_TOKENS,
            response_format={"type": "json_object"},
        )
        payload = json.loads(result.content or "{}")
    except Exception as exc:  # noqa: BLE001 - any provider failure degrades, never raises
        logger.warning("spec extraction failed, using the rule pass alone: %s", exc)
        return Extraction()

    elapsed_ms = int((time.monotonic() - started) * 1000)

    items = (payload.get("specs") if isinstance(payload, dict) else None) or []
    # The words the model read each value from, kept beside the validation rather than
    # inside it: `_validated_pairs` is shared with the enquiry reader, which has no
    # evidence to carry, and widening it for one caller would put an unused field on
    # every customer sentence.
    evidence_by_key = {
        str(item.get("key") or "").strip(): str(item.get("evidence") or "").strip()
        for item in items
        if isinstance(item, dict)
    }
    specs = _validated_pairs(items, index, open_values, one_per_key=True)
    for entry in specs:
        entry["evidence"] = evidence_by_key.get(entry["key"], "")

    try:
        db.add(
            AIAssistantUsageLog(
                user_id=user_id,
                feature="spec_extract",
                provider=provider_name,
                model=model_name,
                prompt_tokens=int(result.prompt_tokens or 0),
                completion_tokens=int(result.completion_tokens or 0),
                total_tokens=int(result.total_tokens or 0),
                cached_tokens=int(getattr(result, "cached_tokens", 0) or 0),
                tool_calls_count=0,
                response_time_ms=elapsed_ms,
                was_answered=bool(specs),
            )
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - never fail a reading over bookkeeping
        logger.warning("spec extraction: usage log failed: %s", exc)
        db.rollback()

    return Extraction(specs=specs, source="semantic", model=model_name)
