"""Entity reference resolver.

Given a free-text user query, extract "code-like" tokens and look each one up in parallel
across the key business entities (products, orders, shipments, customers, suppliers,
warehouses, SPOs, GRNs, promotions). Returns a structured resolution that the AI assistant
can inject into the LLM prompt so the model does not have to guess what entity a code refers
to.

Design notes
------------
- Deterministic SQL only. No RAG / embedding call. Each probe is an indexed equality or
  ILIKE lookup so the whole resolver runs in tens of milliseconds.
- No UUIDs are ever returned. We keep the resolver user-facing: it returns business codes
  and a minimal display payload (name, status, ETA...).
- If zero entities match a token, the token is returned in `unresolved` so the LLM can
  tell the user "no record found for X" rather than hallucinating a match.
- Tokens that look like ordinary English words are filtered via a short stopword list and
  a minimum-length / digit-presence requirement.
"""
from __future__ import annotations

import collections
import copy
import functools
import logging
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Iterable, Optional

from functools import reduce
from operator import add

from sqlalchemy import and_, case, func, or_, text, true
from sqlalchemy.orm import Session

from app.models.base import UNSET, get_company_scope
from app.models.forms import Form
from app.models.inventory import Warehouse
from app.models.marketing import Promotion
from app.models.order import Customer, Order, OrderStatus, Transporter
from app.models.procurement import (
    InboundShipment,
    PickingHeader,
    SPOAllocation,
    Supplier,
)
from app.models.certificate import Certificate, CertificateRevision
from app.models.product import Product, ProductAttachment, chat_searchable_products
from app.models.resources import Attachment, AttachmentType
# Imported rather than redefined, so the value cannot drift between the two modules. A
# draft (proforma-created) shipment must not be resolvable by container/BOL/invoice number
# here either - `incoming_stock_service` already excludes it from every list read, and an
# assistant that could still NAME the draft via this resolver would ask incoming_list about
# it and get told "nothing incoming for container X" instead of never hearing of it.
from app.services.scm import spo_supply
from app.services.scm.proforma_invoice_service import _DRAFT_SHIPMENT_STATUS


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Entity-type aliases
# --------------------------------------------------------------------------- #
# Resolver uses canonical internal names (e.g. "customer_order"). External callers
# sometimes pass UI-facing synonyms (e.g. "delivery_order"). Map them once here so
# both `allowed_entity_types` filtering AND positional token<->type pairing stay
# consistent regardless of which label the caller used.
_ENTITY_TYPE_ALIASES: dict[str, str] = {
    "delivery_order": "customer_order",
    "do": "customer_order",
    "order": "customer_order",
    "sales_order": "customer_order",
    # Product-domain hints. Caller uses these labels as `domain_hint` to scope
    # ambiguous aliases (brand / category) to product probes.
    # Product sets: the flyer code that names an assembly. "kit" is what people
    # say out loud; the glossary term is Product Set.
    "product_set": "product_set",
    "product_sets": "product_set",
    "set": "product_set",
    "kit": "product_set",
    "product_attachment": "product",
    "product_attachments": "product",
    "master_products": "product",
    "master_product": "product",
    "products": "product",
    # Attachment type catalog (AttachmentType row, NOT a file row). Resolves a
    # free-text doc-class label like "catalogue", "brochure", "spec sheet" into
    # the canonical AttachmentType UUID, suitable for `attachment_type_ids` /
    # `attachment_type_id` filters on attachment list tools.
    "attachment-type": "attachment_type",
    "attachmenttype": "attachment_type",
    "doc_type": "attachment_type",
    "document_type": "attachment_type",
    "file_type": "attachment_type",
    # Brand / category handled via `_ENTITY_TYPE_EXPANSIONS` below (one-to-many
    # fan-out), NOT the 1:1 alias map. Listed here as identity so
    # `_canonical_entity_type` does not drop them before expansion runs.
    "brand": "brand",
    "brands": "brand",
    "category": "category",
    "categories": "category",
    "product_category": "category",
    "product_categories": "category",
}


# One-to-many fan-out for canonical names that have NO dedicated probe but map
# to one or more concrete entity types in the resolver. Applied after
# `_canonical_entity_type` when building the `allowed` set. Brand / category
# resolve to promotion only - keeping the surface narrow (promotions group
# products by brand + category, which is what the n8n promo agent needs).
_ENTITY_TYPE_EXPANSIONS: dict[str, frozenset[str]] = {
    "brand": frozenset({"promotion"}),
    "category": frozenset({"promotion"}),
}


# Domain-scoped overrides for one-to-many expansions. When the caller passes
# `domain_hint`, ambiguous aliases like brand / category gain a different
# meaning. Example - domain_hint="order" + token="super ceramic": the user is
# almost certainly naming a customer / debtor, not a promotion bucket. Routing
# the expansion to {customer, customer_order, transporter} keeps the resolver
# inside the order domain.
_DOMAIN_HINT_EXPANSIONS: dict[str, dict[str, frozenset[str]]] = {
    "order": {
        "brand": frozenset({"customer", "customer_order", "transporter"}),
        "category": frozenset({"customer", "customer_order", "transporter"}),
    },
    "customer_order": {
        "brand": frozenset({"customer", "customer_order", "transporter"}),
        "category": frozenset({"customer", "customer_order", "transporter"}),
    },
    "delivery_order": {
        "brand": frozenset({"customer", "customer_order", "transporter"}),
        "category": frozenset({"customer", "customer_order", "transporter"}),
    },
    # Product domain - `product_attachment` / `master_products` / `products`
    # all canonicalize to `product` via `_ENTITY_TYPE_ALIASES`. Brand / category
    # in that context should hit product probes (products carry brand_id +
    # category_id directly) instead of fanning out to promotion.
    "product": {
        "brand": frozenset({"product"}),
        "category": frozenset({"product"}),
    },
}


def _expand_entity_types(
    types: Iterable[str],
    domain_hint: Optional[str] = None,
) -> frozenset[str]:
    """Canonicalize + fan-out one-to-many expansions into a concrete probe set.

    `_canonical_entity_type` only does 1:1 alias rewriting. Brand / category
    have no probes of their own and need to map to a SET of concrete probe
    types. When `domain_hint` is supplied AND it has an override in
    `_DOMAIN_HINT_EXPANSIONS`, the override takes precedence over the default
    `_ENTITY_TYPE_EXPANSIONS` map - keeps brand / category scoped to the
    domain the caller actually cares about.
    """
    hint_canon = _canonical_entity_type(domain_hint) if domain_hint else None
    overrides = _DOMAIN_HINT_EXPANSIONS.get(hint_canon or "", {}) if hint_canon else {}
    out: set[str] = set()
    for raw in types:
        if not raw:
            continue
        canon = _canonical_entity_type(raw)
        expansion = overrides.get(canon) or _ENTITY_TYPE_EXPANSIONS.get(canon)
        if expansion:
            out.update(expansion)
        else:
            out.add(canon)
        # n8n's `product` hint has to reach the set probe too: n8n names a
        # flyer code with domain_hint="product" (it has no reason to know the
        # glossary term "product_set"), and `_TIER1_PROBES` only runs a probe
        # when its `produces` set intersects `allowed`. Additive, not a
        # replacement of `canon` — a caller that filtered on `product` still
        # gets ordinary products, PLUS the set probe becomes reachable.
        if canon == "product":
            out.add("product_set")
    return frozenset(out)


def _canonical_entity_type(et: str) -> str:
    key = (et or "").strip().lower()
    return _ENTITY_TYPE_ALIASES.get(key, key)


def _build_token_type_map(
    tokens: list[str] | None,
    allowed_entity_types: Iterable[str] | None,
) -> Optional[dict[str, str]]:
    """Deprecated - positional pairing is disabled.

    Historically returned a positional token -> canonical_entity_type map when
    caller passed parallel lists of equal length, so token[i] would be resolved
    ONLY against allowed_entity_types[i]. This behaviour surprised callers who
    wanted each token resolved against EVERY allowed type (set-filter mode),
    e.g. tokens=["Sorento water closet", "latest promo"] with
    allowed_entity_types=["product", "promotion"] - they expect both tokens
    probed against both types.

    Always returns None now, so every code path falls through to the set-filter
    branch: `allowed` becomes the canonical set of all supplied types, and each
    surviving probe sees every token. Callers that need 1:1 pairing must make
    multiple resolve calls, one per (token, type).
    """
    return None


# --------------------------------------------------------------------------- #
# Token extraction
# --------------------------------------------------------------------------- #
# Code-like token: contains at least one letter AND at least one digit, min length 3.
# Allows letters, digits, hyphens, slashes, underscores, dots - but not pure numbers.
# Examples accepted: ACC-SRT1024, RF2601-025, FJ24041192, MSCU5475129, CB310-BL, CB313, PL.2026.001
# Examples rejected: the, and, 2026 (all digits), order, RF (no digit), product
_CODE_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9]*(?:[-_./][A-Za-z0-9]+)+"  # multi-segment: e.g. ACC-SRT1024, RF2601-025
    r"|[A-Za-z]+\d+[A-Za-z0-9]*"  # letters-then-digits: FJ24041192, CB313, MSCU5475129
    r"|\d+[A-Za-z]+[A-Za-z0-9]*"  # digits-then-letters: 10KG, 24HR
)

# Tokens that match the regex but are almost always English words or noise. Keep SHORT
# - we prefer false positives (extra SQL work) over false negatives (missed entity).
_STOPWORDS: frozenset[str] = frozenset(
    {
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "today",
        "tomorrow",
        "yesterday",
        "please",
        "thanks",
        "thank",
    }
)


# Marker-driven name capture. We only run this when the user/agent has explicitly
# tagged the value with a customer/debtor/client/account marker, to avoid pulling
# every random capitalised word as a "name".
# - "customer is Jayson", "customer: Jayson Lim", "customer name IJM Land"
# - "debtor jayson", "debtor name IJM Land Sdn Bhd"
# - "client: ABC Corp", "account name Pang Holdings"
# The lookahead stops the (non-greedy) capture before common verbs / connectors so
# "client: ABC Corp ordered RF2601-025" yields just "ABC Corp" rather than the rest.
_NAME_STOP_WORDS_RE = (
    r"(?:and|with|for|of|the|a|an|is|was|has|have|had|who|that|"
    r"order(?:ed|ing|s)?|buy(?:s|ing)?|bought|"
    r"want(?:s|ed|ing)?|need(?:s|ed|ing)?|file(?:d|s)?|filing|"
    r"product|sku|date|do|delivery|complaint|complain(?:t|ing|s|ed)?|"
    r"quantity|qty|in|on|at|by|to|from)"
)
_NAME_MARKER_RE = re.compile(
    r"(?:customer(?:\s+name)?|debtor(?:\s+name)?|client(?:\s+name)?|account(?:\s+name)?|company)"
    r"\s*(?:is|are|:|=|->|→| - |-)?\s*"
    r"([A-Za-z][A-Za-z0-9&'.\- ]{1,60}?)"
    r"(?=$|[,;.\n!?]|\s+" + _NAME_STOP_WORDS_RE + r"\b)",
    re.IGNORECASE,
)


# Free-word scan for names that lack an explicit marker. We extract alphabetic
# words/phrases (>=3 chars, not a stopword) so the resolver's Tier-1 debtor probe can
# do a single bulk `LOWER(debtor_name) IN (...)` lookup. This stays cheap because the
# probe runs ONE indexed query regardless of how many candidate words we feed it, and
# everything that doesn't match a real debtor name is dropped.
_FREEWORD_STOPWORDS: frozenset[str] = frozenset(
    {
        # functional / common English noise
        "the", "and", "for", "with", "from", "into", "onto", "this", "that", "those",
        "these", "their", "there", "then", "than", "what", "when", "where", "which",
        "who", "whom", "whose", "want", "wants", "wanted", "need", "needs", "needed",
        "have", "has", "had", "having", "is", "are", "was", "were", "been", "being",
        "do", "does", "did", "doing", "done",
        # CRM domain words that should never be matched as a customer name
        "complaint", "complaints", "complain", "complaining", "defect", "defects",
        "defective", "broken", "leak", "leaking", "warranty", "salesperson",
        "delivery", "deliveries", "delivered", "delivering", "ordered", "ordering",
        "order", "orders", "product", "products", "sku", "skus", "quantity", "qty",
        "customer", "customers", "debtor", "debtors", "client", "clients", "company",
        "companies", "account", "accounts", "invoice", "invoices", "shipment",
        "shipments", "stock", "incoming", "outgoing", "pending", "cancelled",
        "category", "brand", "promotion", "supplier", "suppliers", "warehouse",
        "warehouses", "discount", "price", "prices",
        # date words
        "today", "tomorrow", "yesterday", "morning", "afternoon", "evening", "night",
        "week", "weeks", "month", "months", "year", "years", "day", "days",
        "january", "february", "march", "april", "may", "june", "july", "august",
        "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        # short fillers
        "find", "look", "looking", "search", "searching", "show", "list", "lookup",
        "file", "filed", "files", "filing", "submit", "submits", "submitted",
        "submitting", "create", "created", "creating", "raise", "raised", "raising",
        "report", "reported", "reporting", "please", "thanks", "thank", "ok",
        "okay", "yes", "yeah", "no", "not",
        "all", "any", "some", "more", "less", "new", "old", "in", "on", "at", "to",
        "by", "of", "or", "if", "as", "be", "it", "i", "me", "my", "we", "us", "our",
        "you", "your", "he", "she", "him", "her", "they", "them",
        # form fields commonly mentioned
        "warranty", "type", "types", "address", "phone", "email", "name", "names",
        "person", "title", "project",
    }
)
_FREEWORD_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'.\-&]+")


def _extract_name_tokens(query: str, *, max_candidates: int) -> list[str]:
    """Pull name-like phrases that follow `customer`/`debtor`/`client` markers.

    Conservative: only fires on explicit markers, so "the customer is Jayson" yields
    `["Jayson"]` but "I am Jayson today" does not.
    """
    if not query:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for match in _NAME_MARKER_RE.finditer(query):
        raw = (match.group(1) or "").strip(" ,.;:-_'")
        if not raw or len(raw) < 2:
            continue
        # Strip trailing filler that slipped past the lookahead (defensive).
        cleaned = re.sub(
            r"\s+(?:has|who|that|with|for|of|the|order(?:ed|ing|s)?|bought|buy(?:s|ing)?)\s*.*$",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()
        if not cleaned or cleaned.lower() in _STOPWORDS:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= max_candidates:
            break
    return out



def _extract_freeword_tokens(query: str, *, max_candidates: int) -> list[str]:
    """Pull alphabetic word/phrase candidates that may be customer (debtor) names.

    Algorithm:
        1. Tokenise into individual alphabetic words.
        2. Drop stopwords / short fragments / month names / etc.
        3. Group consecutive non-stopword survivors into phrases (max 4 words each)
           so multi-word debtors like "IJM Land Sdn Bhd" stay together.
        4. Also emit each survivor as a single-word candidate so 1-word debtors
           ("Jayson") still resolve when the multi-word group misses.

    Used as a free-text fallback when the user just writes "Find DO for jayson Feb
    2026" with no explicit `customer:` marker. The resolver's debtor probe runs in
    a single bulk query so feeding it a handful of words is cheap; everything that
    doesn't hit a real `Order.debtor_name` is silently dropped.
    """
    if not query:
        return []
    surviving: list[str] = []  # words that pass stopword filtering, in order
    is_separator: list[bool] = []  # parallel flag: True when we crossed a stopword
    last_end = 0
    for match in _FREEWORD_TOKEN_RE.finditer(query):
        raw = (match.group(0) or "").strip(" ,.;:-_'")
        if not raw:
            continue
        lower = raw.lower()
        if lower in _FREEWORD_STOPWORDS or lower in _STOPWORDS:
            # Mark the next survivor as a phrase boundary.
            if surviving:
                is_separator[-1] = True
            last_end = match.end()
            continue
        if len(raw) < 3:
            if surviving:
                is_separator[-1] = True
            last_end = match.end()
            continue
        # Detect punctuation / digit / boundary between this word and the last
        # survivor - also treat as a phrase boundary.
        if surviving:
            gap = query[last_end : match.start()]
            if any(c in gap for c in (",", ";", ":", "\n", "!", "?", "(", ")", "/", "|")) or any(
                c.isdigit() for c in gap
            ):
                is_separator[-1] = True
        surviving.append(raw)
        is_separator.append(False)
        last_end = match.end()

    # Build phrases by grouping survivors until a separator flag.
    phrases: list[str] = []
    current: list[str] = []
    for word, sep in zip(surviving, is_separator):
        current.append(word)
        if sep or len(current) >= 4:
            phrases.append(" ".join(current))
            current = []
    if current:
        phrases.append(" ".join(current))

    out: list[str] = []
    seen: set[str] = set()
    # Emit phrases first (more specific), then individual words.
    for candidate in phrases + surviving:
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
        if len(out) >= max_candidates:
            break
    return out


def extract_candidate_tokens(query: str, *, max_candidates: int = 8) -> list[str]:
    """Pull code-like tokens from the user's free-text query.

    Returns at most `max_candidates` unique tokens, preserving order of first appearance.
    Also includes name-like phrases that follow customer/debtor/client markers, so
    free-text customer references can be resolved against `Order.debtor_name`.
    """
    if not query:
        return []
    raw = _CODE_RE.findall(query)
    seen: set[str] = set()
    out: list[str] = []
    for tok in raw:
        t = tok.strip(".-_/").strip()
        if not t:
            continue
        if len(t) < 3:
            continue
        if t.lower() in _STOPWORDS:
            continue
        # Require BOTH a digit and a letter - rejects pure numbers ("2026", phone fragments)
        # and pure words simultaneously.
        if not (any(c.isdigit() for c in t) and any(c.isalpha() for c in t)):
            continue
        key = t.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= max_candidates:
            break

    # Append name-marker tokens (e.g. "customer is Jayson" → "Jayson"). These are
    # alphabetic and won't pass the code-token filter, so we add them here. They get
    # routed to `_probe_customer_debtor_name` and `_prefix_probe_customer_debtor_name`.
    remaining = max_candidates - len(out)
    if remaining > 0:
        for name_tok in _extract_name_tokens(query, max_candidates=remaining):
            key = name_tok.upper()
            if key in seen:
                continue
            seen.add(key)
            out.append(name_tok)
            if len(out) >= max_candidates:
                break
    return out


def extract_freeword_candidates(query: str, *, max_candidates: int = 6) -> list[str]:
    """Public helper exposing free-word debtor candidates (used by the resolver).

    Kept separate from `extract_candidate_tokens` so the regular code-token list
    stays small; these candidates are only fed to the `Order.debtor_name` probe.
    """
    return _extract_freeword_tokens(query, max_candidates=max_candidates)


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #
@dataclass
class ResolvedEntity:
    """One hit for a candidate token."""

    entity_type: str  # product | customer_order | customer | inbound_shipment | spo_allocation | grn | warehouse | supplier | promotion | transporter | form | attachment | attachment_type
    canonical_code: str  # the business code the user should pass to tools (e.g. order_number)
    uuid: Optional[str] = None  # the row's primary key - required for tools that accept UUID-only inputs
    display: dict[str, Any] = field(default_factory=dict)
    match_field: str = ""  # which column matched (e.g. "product_code", "product_name")
    match_tier: str = "exact"  # "exact" | "prefix" | "substring" | "embedding" | "head_code"
    similarity: Optional[float] = None  # cosine similarity for embedding-tier matches
    # Owning company, when the entity type is company-scoped (NULL for a shared row
    # and absent entirely for a global type like attachment_type). Emitted so a
    # caller holding a multi-company scope can tell a REAL ambiguity ("two different
    # products") from a bookkeeping one ("the same code in each company") and only
    # prompt for the former. See `_attach_company_info`.
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    # The owning company's short CODE ("SRT", "MOCHA"), alongside its name. The chatbot's
    # ambiguous-customer picker prints it next to the account name, because the SAME
    # customer name exists once per company ledger ("A CRAFT IDEA SDN BHD (SRT)") and the
    # code is what tells the two lines apart in a WhatsApp-width list.
    company_code: Optional[str] = None
    # The exact text this row was matched AGAINST, set by the AND probes so
    # `token_word_coverage_for_rows` can report which query words actually
    # landed. It is the probe's blob, NOT everything in `display`: the product
    # probe matches on `product_code` alone by design, so scoring a word
    # against `product_name` would report a match the probe never made.
    # Serialised as the transport key `_match_blob` on AND intersection rows
    # only; the API layer (`references._attach_and_coverage`) strips it before
    # the response leaves the process.
    match_blob: Optional[str] = None


@dataclass
class TokenResolution:
    token: str
    matches: list[ResolvedEntity] = field(default_factory=list)
    ambiguous: bool = False  # True when we found multiple candidates but picked none
    # Fuzzy trigram "did you mean" neighbours for a token that produced NO exact/
    # prefix/embedding match. Purely entity-level (NOT domain-data-gated - that is
    # the list tools' job): "SRTKT71SX unknown → did you mean SRTKT71SS?". Emitted
    # so callers get a suggestion inline without a second neighbour lookup. Usually
    # empty when `matches` is non-empty; the one exception (D10b) is a token whose
    # every match is a type outside the caller's OWN `allowed_entity_types` (a flyer/set
    # code hitting `product_set` when the caller asked for `product`) - there `matches`
    # keeps that row (byte identity for callers reading it) and `alternatives` ALSO gets
    # populated, because to the caller that match is as useless as no match at all. Does
    # not affect `resolved`, which still reads `matches` alone.
    alternatives: list[ResolvedEntity] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        # A single confident match. Ambiguous tokens are NOT resolved - the LLM must ask.
        return bool(self.matches) and not self.ambiguous

    @property
    def confident_match(self) -> Optional[ResolvedEntity]:
        """The single canonical match to pass to other tools, or None if ambiguous / unresolved."""
        if self.ambiguous or not self.matches:
            return None
        return self.matches[0]


@dataclass
class ResolutionResult:
    tokens: list[str]
    resolutions: list[TokenResolution]
    elapsed_ms: float

    @property
    def unresolved_tokens(self) -> list[str]:
        """Tokens with zero candidate matches (NOT tokens that are ambiguous)."""
        return [r.token for r in self.resolutions if not r.matches]

    @property
    def ambiguous_tokens(self) -> list[str]:
        return [r.token for r in self.resolutions if r.ambiguous]

    @property
    def has_any_match(self) -> bool:
        return any(r.resolved for r in self.resolutions)

    def to_prompt_block(self) -> str:
        """Render an authoritative block for the LLM. Empty string when nothing useful."""
        if not self.resolutions:
            return ""
        lines: list[str] = []
        resolved = [r for r in self.resolutions if r.resolved]
        ambiguous = [r for r in self.resolutions if r.ambiguous]
        unresolved = [r.token for r in self.resolutions if not r.matches]
        if resolved:
            lines.append("Resolved references in user query (authoritative \u2014 use these):")
            for tr in resolved:
                for match in tr.matches:
                    display_bits = [f"{k}={v}" for k, v in match.display.items() if v not in (None, "", [])]
                    desc = ", ".join(display_bits)
                    tier_note = ""
                    if match.match_tier == "prefix":
                        tier_note = f" (fuzzy prefix match on {match.match_field})"
                    elif match.match_tier == "substring":
                        tier_note = f" (fuzzy substring match on {match.match_field})"
                    elif match.match_tier == "embedding":
                        sim_str = f"{match.similarity:.2f}" if match.similarity is not None else "?"
                        tier_note = f" (semantic match, similarity={sim_str})"
                    elif match.match_tier == "head_code":
                        tier_note = " (matched by leading code; trailing words ignored)"
                    lines.append(
                        f'- "{tr.token}" \u2192 {match.entity_type} (canonical_code={match.canonical_code})'
                        + tier_note
                        + (f" [{desc}]" if desc else "")
                    )
        if ambiguous:
            if lines:
                lines.append("")
            lines.append(
                "Ambiguous references in user query (multiple candidates \u2014 ask the user to pick one, "
                "do NOT call any tool yet):"
            )
            for tr in ambiguous:
                lines.append(f'- "{tr.token}" could be any of:')
                for match in tr.matches:
                    display_bits = [f"{k}={v}" for k, v in match.display.items() if v not in (None, "", [])]
                    desc = ", ".join(display_bits)
                    lines.append(
                        f"    \u00b7 {match.entity_type} (canonical_code={match.canonical_code})"
                        + (f" [{desc}]" if desc else "")
                    )
        if unresolved:
            if lines:
                lines.append("")
            lines.append("Unresolved references (tell the user no record was found, do not guess):")
            for t in unresolved:
                lines.append(
                    f'- "{t}" \u2014 no matching product, order, shipment, customer, supplier, warehouse, SPO, GRN, or promotion.'
                )
        return "\n".join(lines).strip()

    def to_query_hint(self) -> str:
        """Short hint appended to the reformulated query so the RAG tool picker sees entity types."""
        if not self.resolutions:
            return ""
        bits: list[str] = []
        for tr in self.resolutions:
            if tr.resolved:
                types = ", ".join(sorted({m.entity_type for m in tr.matches}))
                bits.append(f'"{tr.token}" is {types}')
            elif tr.ambiguous:
                types = ", ".join(sorted({m.entity_type for m in tr.matches}))
                bits.append(f'"{tr.token}" ambiguous ({types})')
            else:
                bits.append(f'"{tr.token}" unresolved')
        return "Resolved references: " + "; ".join(bits) + "."

    def as_dict(self) -> dict[str, Any]:
        return {
            "tokens": self.tokens,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "resolutions": [
                {
                    "token": tr.token,
                    "resolved": tr.resolved,
                    "ambiguous": tr.ambiguous,
                    "matches": [
                        {
                            "entity_type": m.entity_type,
                            "canonical_code": m.canonical_code,
                            "uuid": m.uuid,
                            "match_field": m.match_field,
                            "match_tier": m.match_tier,
                            "similarity": m.similarity,
                            "company_id": m.company_id,
                            "company_name": m.company_name,
                            "company_code": m.company_code,
                            "display": m.display,
                        }
                        for m in tr.matches
                    ],
                    "alternatives": [
                        {
                            "entity_type": a.entity_type,
                            "canonical_code": a.canonical_code,
                            "uuid": a.uuid,
                            "match_field": a.match_field,
                            "match_tier": a.match_tier,
                            "similarity": a.similarity,
                            "company_id": a.company_id,
                            "company_name": a.company_name,
                            "company_code": a.company_code,
                            "display": a.display,
                        }
                        for a in tr.alternatives
                    ],
                }
                for tr in self.resolutions
            ],
            "unresolved_tokens": self.unresolved_tokens,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _first_nonempty(values: Iterable[Any]) -> Any:
    for v in values:
        if v not in (None, "", []):
            return v
    return None


# --------------------------------------------------------------------------- #
# Per-entity probes
# --------------------------------------------------------------------------- #
def _strip_all_ws(value: str) -> str:
    """Collapse every dash and whitespace run to empty (§3a dash/ws-insensitive).

    For code-style tokens where the agent typed (or the user pasted) a stray
    dash or space inside what should be one contiguous code - e.g.
    'cgb9032b- new' → 'cgb9032bnew', 'srtkt71-ss' → 'srtkt71ss'. So dash and
    no-dash variants of the same code (`SRTWT7438GM` ≡ `SRTWT7438-GM`) flatten
    to one form and match exactly. Only `[-\\s]` is stripped; `/ . _` stay
    significant."""
    return re.sub(r"[-\s]+", "", value or "")


def _ws_insensitive_lower(col):
    """Postgres expression: `lower(regexp_replace(col, '[-\\s]+', '', 'g'))`.

    Pair with python-side `_strip_all_ws(token.lower())` so code-style fields
    (product_code, order_number, shipment_number, supplier_code, ...) match
    whether either side carries stray dashes or whitespace (§3a). Cheap on
    small lookup tables; for large tables consider a functional index if hot."""
    return func.lower(func.regexp_replace(col, r"[-\s]+", "", "g"))


def _alnum_casefold(value: str) -> str:
    """Casefold + strip every character that is not `[0-9A-Za-z]`.

    Stricter than `_strip_all_ws` (dash/whitespace only): the warehouse exact-code rule
    (owner ruling 8 Sep 2026, chatbot-warehouse-entity-and-last-in) needs "brw ib",
    "brwib", "BRW-IB" and "Brw_IB" to all normalize to the same string, and a caller can
    type an underscore or other punctuation a code-style field never stores."""
    return re.sub(r"[^0-9A-Za-z]+", "", value or "").casefold()


def _alnum_insensitive_lower(col):
    """Postgres twin of :func:`_alnum_casefold`: `lower(regexp_replace(col, '[^0-9A-Za-z]+', '', 'g'))`."""
    return func.lower(func.regexp_replace(col, r"[^0-9A-Za-z]+", "", "g"))


def _norm_sql(expr: str) -> str:
    """Raw-SQL twin of :func:`_ws_insensitive_lower`, for ``text()`` blocks.

    Same expression, spelled for the hand-written trigram probes that cannot use
    the ORM construct. Keep the two in lockstep: a divergence would silently
    normalize one tier differently from the next, and would drop the functional
    indexes that migration 410 builds on exactly this expression."""
    return "lower(regexp_replace(" + expr + ", '[-\\s]+', '', 'g'))"


# Minimum length (after dash/whitespace-stripping) before a normalized CONTAINS
# clause is allowed. Below it, dropping separators lets a short token span word
# boundaries and match almost anything ("nkl" hitting "MOON KLANG"). Anchored
# prefix matching carries no such risk, so `_norm_prefix` is not guarded.
_NORM_MIN_LEN = 4


def _norm_prefix(col, token: str):
    """`col ILIKE 'token%'` OR the same match with both sides normalized.

    One of the two standard matching primitives (see `_norm_contains`). Every
    resolver tier goes through them so a token whose separators were stripped
    upstream - n8n strips dashes and whitespace from EVERY entity token before
    calling /system/references/resolve, because STT spaces product codes
    ("SRT WC286SH") - still reaches a stored value that keeps them
    ("MASTILEKLANG" -> "MASTILE KLANG SDN BHD"). The plain ILIKE is always kept
    alongside, so the normalized clause can only ADD matches, never remove one.
    """
    clauses = [col.ilike(f"{token}%")]
    norm = _strip_all_ws(token).lower()
    if norm:
        clauses.append(_ws_insensitive_lower(col).like(f"{norm}%"))
    return or_(*clauses)


def _norm_contains(col, token: str):
    """`col ILIKE '%token%'` OR the same match with both sides normalized.

    Guarded by `_NORM_MIN_LEN`: an unanchored normalized match on a very short
    token would span word boundaries. These are sequential scans either way; if
    one turns hot, the fix is an expression index on
    `lower(regexp_replace(col, '[-\\s]+', '', 'g'))`.
    """
    clauses = [col.ilike(f"%{token}%")]
    norm = _strip_all_ws(token).lower()
    if len(norm) >= _NORM_MIN_LEN:
        clauses.append(_ws_insensitive_lower(col).like(f"%{norm}%"))
    return or_(*clauses)


def _norm_token_map(tokens: Iterable[str]) -> dict[str, str]:
    """`{normalized_token: original_token}` for the EXACT tiers.

    Lets an exact-match probe accept a separator-stripped token and still map
    the matched row back to the token the caller sent. Short tokens are dropped
    for the same reason `_norm_contains` guards them."""
    return {
        _strip_all_ws(t).lower(): t
        for t in tokens
        if t and len(_strip_all_ws(t)) >= _NORM_MIN_LEN
    }


# A leading code-shaped word: letters, then any run of letters/digits/dashes,
# with at least one digit somewhere in it ("SRTWB8004", "ZZB6201", "MUB6201").
# Mirrors the n8n parser's own `prodTok` regex, so a raw text this pattern
# rejects (no digit in the head, e.g. bare "MUB") is not a code candidate on
# either side. Used only by the head-code retry in `resolve_references`.
_HEAD_CODE_RE = re.compile(r"^[A-Za-z]{2,}[A-Za-z0-9-]*\d[A-Za-z0-9-]*")


def _probe_product(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Exact match on product_code (case-insensitive, whitespace-insensitive).

    Both sides are whitespace-stripped so 'cgb9032b- new' matches DB code
    'CGB9032B-NEW' (and vice versa, if a DB row was seeded with a stray space)."""
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    # Build (original_token, normalized_token) pairs so we can map matched rows
    # back to the caller's token after the SQL comparison.
    normalized_tokens = [_strip_all_ws(t.lower()) for t in tokens]
    norm_to_token = dict(zip(normalized_tokens, tokens))
    # A placeholder's exact code is still not an answer: that is the whole point
    # of the flag (issue #300), so the filter sits on the exact tier too.
    rows = (
        db.query(Product.id, Product.product_code, Product.product_name, Product.is_active)
        .filter(_ws_insensitive_lower(Product.product_code).in_(list(norm_to_token.keys())))
        .filter(chat_searchable_products())
        .all()
    )
    for pid, code, name, is_active in rows:
        norm = _strip_all_ws(str(code).lower())
        token = norm_to_token.get(norm)
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="product",
                canonical_code=code,
                uuid=str(pid) if pid else None,
                match_field="product_code",
                display={"product_name": name, "is_active": bool(is_active)},
            )
        )
    return result


def _probe_product_set(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """A flyer code answers with ONE entity carrying its members.

    `SRTWC8608-RL` is printed on a flyer and asked for on WhatsApp, but no product
    carries it - the catalogue holds a pedestal, a cistern and a seat cover. The
    exact product probe finds nothing and the customer is told the product does
    not exist. This is the probe that answers instead.

    NOT a fan-out. Three top-level product entities would lose the thing the
    customer named, and once a set carries a price of its own it IS an entity.

    NO PRICE in the response: a stock question gets stock, and the price path has
    its own entitlement gates (UAC D7).

    ORM only. Company isolation is injected by the `do_orm_execute` listener into
    every ORM SELECT touching a `CompanyScopedMixin` model; `ProductSet` carries
    it, so another company's set is invisible here and its members are never
    reached. `ProductSetMember` is deliberately unscoped and is read only THROUGH
    its scoped parent, the same shape as `certificate_products` through
    `Certificate`. A raw `text()` query would bypass the listener entirely.
    """
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result

    from app.models.product_set import ProductSet, ProductSetMember

    normalized_tokens = [_strip_all_ws(t.lower()) for t in tokens]
    norm_to_token = dict(zip(normalized_tokens, tokens))

    rows = (
        db.query(ProductSet)
        .filter(_ws_insensitive_lower(ProductSet.set_code).in_(list(norm_to_token.keys())))
        .all()
    )
    if not rows:
        return result

    members_by_set: dict[str, list] = {}
    for product_set in rows:
        members_by_set[product_set.id] = (
            db.query(ProductSetMember)
            .filter(ProductSetMember.product_set_id == product_set.id)
            .order_by(ProductSetMember.sort_order)
            .all()
        )

    product_ids = [m.product_id for members in members_by_set.values() for m in members]
    available: dict[str, int] = {}
    if product_ids:
        from app.models.inventory import Stock

        for pid, total in (
            db.query(Stock.product_id, func.sum(Stock.quantity_available))
            .filter(Stock.product_id.in_(list(set(product_ids))))
            .group_by(Stock.product_id)
            .all()
        ):
            available[str(pid)] = int(total or 0)

    for product_set in rows:
        token = norm_to_token.get(_strip_all_ws(str(product_set.set_code).lower()))
        if not token:
            continue

        rendered = []
        complete = None
        limiting = None
        for member in members_by_set.get(product_set.id, []):
            product = member.product
            if product is None:
                continue
            have = available.get(str(member.product_id), 0)
            quantity = member.quantity or 1
            rendered.append(
                {
                    "product_code": product.product_code,
                    "description": product.description or product.product_name,
                    "quantity": float(quantity),
                    "available": have,
                    "is_discontinued": bool(product.is_discontinued),
                    # For n8n's fan-out: the existing per-product MCP tools
                    # (`crm_inventory_stock_balance_list`, `crm_master_products_list`,
                    # `crm_marketing_promotion_products_list`,
                    # `crm_incoming_stock_by_product`) already take `product_ids`.
                    # This is what feeds them - no new tool needed. Machine payload,
                    # not UI, so the UUID rule does not apply (matches.uuid already
                    # carries one at the top level).
                    "uuid": str(member.product_id),
                }
            )
            # A discontinued member supplies nothing: the set survives it, but it
            # cannot be completed, and the reason is that member's own code.
            supplies = 0 if product.is_discontinued else int(have // float(quantity))
            if complete is None or supplies < complete:
                complete, limiting = supplies, product.product_code

        result[token].append(
            ResolvedEntity(
                entity_type="product_set",
                canonical_code=product_set.set_code,
                uuid=str(product_set.id),
                match_field="set_code",
                display={
                    "name": product_set.name,
                    "member_count": len(rendered),
                    "complete_sets": complete,
                    "limiting_member": limiting,
                    "members": rendered,
                },
            )
        )
    return result


def _probe_customer_order(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Exact match on orders.order_number, joined to order_statuses for the label."""
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    normalized = [_strip_all_ws(t.lower()) for t in tokens]
    norm_to_token = dict(zip(normalized, tokens))
    rows = (
        db.query(
            Order.id,
            Order.order_number,
            Order.debtor_name,
            Order.order_date,
            Order.actual_delivery_date,
            Order.pickup_time,
            Order.transporter,
            Order.is_cancelled,
            OrderStatus.status_name,
            OrderStatus.status_code,
        )
        .outerjoin(OrderStatus, OrderStatus.id == Order.order_status_id)
        .filter(_ws_insensitive_lower(Order.order_number).in_(list(norm_to_token.keys())), Order.deleted_at.is_(None))
        .all()
    )
    for row in rows:
        token = norm_to_token.get(_strip_all_ws(str(row.order_number).lower()))
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="customer_order",
                canonical_code=row.order_number,
                uuid=str(row.id) if row.id else None,
                match_field="order_number",
                display={
                    "customer_name": row.debtor_name,
                    "status": row.status_name or row.status_code,
                    "order_date": _iso(row.order_date),
                    "actual_delivery_date": _iso(row.actual_delivery_date),
                    "pickup_time": row.pickup_time,
                    "transporter": row.transporter,
                    "is_cancelled": bool(row.is_cancelled) if row.is_cancelled is not None else False,
                },
            )
        )
    return result


def _name_hit(name: str | None, token: str) -> bool:
    """Did `token` match `name` - plainly, or only after normalization?

    Used to label match_field: a row pulled in by the normalized clause has the
    token nowhere in the raw name, so the plain `in` test alone would mislabel
    the hit as a phone match."""
    if not name:
        return False
    lowered_name = name.lower()
    if token.lower() in lowered_name:
        return True
    norm_token = _strip_all_ws(token).lower()
    return bool(len(norm_token) >= _NORM_MIN_LEN and norm_token in _strip_all_ws(lowered_name))


_WORD_RE = re.compile(r"[a-z0-9]+")


def _whole_word_name_hit(name: str | None, token: str) -> bool:
    """Is `token` whole words of `name`: its words a run of the name's words, or, typed
    without its spaces, a run of them joined ("hanlimtrading" is HANLIM TRADING)? Never
    a part of a word ("wt" is not in "NEWTON")."""
    have = _WORD_RE.findall((name or "").lower())
    want = _WORD_RE.findall((token or "").lower())
    if not have or not want:
        return False
    if any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1)):
        return True
    joined = "".join(want)
    for i in range(len(have)):
        run = ""
        for word in have[i:]:
            run += word
            if run == joined:
                return True
            if len(run) >= len(joined):
                break
    return False


def _phone_hit(phone: str | None, token: str) -> bool:
    digits = "".join(ch for ch in token if ch.isdigit())
    return bool(digits) and digits in "".join(ch for ch in (phone or "") if ch.isdigit())


def _probe_customer(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Exact match on customer_code; fuzzy ILIKE on customer_name / phone_number / email."""
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    lowered = [t.lower() for t in tokens]
    # Exact by customer_code - whitespace-insensitive on both sides so a typed
    # "300- C043" still hits "300-C043".
    norm_to_token = {_strip_all_ws(t.lower()): t for t in tokens}
    rows = (
        db.query(
            Customer.id,
            Customer.customer_code,
            Customer.customer_name,
            Customer.phone_number,
            Customer.email,
            Customer.is_active,
        )
        .filter(_ws_insensitive_lower(Customer.customer_code).in_(list(norm_to_token.keys())))
        .all()
    )
    for cid, code, name, phone, email, is_active in rows:
        token = norm_to_token.get(_strip_all_ws(str(code).lower()))
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="customer",
                canonical_code=code,
                uuid=str(cid) if cid else None,
                match_field="customer_code",
                display={
                    "customer_name": name,
                    "phone_number": phone,
                    "email": email,
                    "is_active": bool(is_active) if is_active is not None else True,
                },
            )
        )
    # Fuzzy on name / phone (only if still unresolved to avoid noise).
    # Phone probe restricted to digit-bearing tokens - keeps name tokens like
    # "chin chun" from accidentally substring-hitting phone numbers, which
    # otherwise floods the fuzzy result with unrelated rows.
    _CUSTOMER_FUZZY_LIMIT = 25
    unresolved = [t for t in tokens if not result[t]]
    for token in unresolved:
        token_has_digit = any(ch.isdigit() for ch in token)
        clauses = [_norm_contains(Customer.customer_name, token)]
        if token_has_digit:
            clauses.append(_norm_contains(Customer.phone_number, token))
        name_or_phone = or_(*clauses)
        rows = (
            db.query(
                Customer.id,
                Customer.customer_code,
                Customer.customer_name,
                Customer.phone_number,
                Customer.email,
            )
            .filter(name_or_phone)
            .limit(_CUSTOMER_FUZZY_LIMIT + 1)
            .all()
        )
        truncated = len(rows) > _CUSTOMER_FUZZY_LIMIT
        rows = rows[:_CUSTOMER_FUZZY_LIMIT]
        for cid, code, name, phone, email in rows:
            if not (_whole_word_name_hit(name, token) or (token_has_digit and _phone_hit(phone, token))):
                # Whole words only (PR #1273 round 7): "wt" is not a customer because
                # "SHOPEE - 260818MRNUPWTS" or "NEWTON BUILDMATE" carry the letters.
                continue
            display = {"customer_name": name, "phone_number": phone, "email": email}
            if truncated:
                display["truncated_more_available"] = True
            result[token].append(
                ResolvedEntity(
                    entity_type="customer",
                    canonical_code=code or name,
                    uuid=str(cid) if cid else None,
                    match_field="customer_name" if _name_hit(name, token) else "phone_number",
                    display=display,
                )
            )
    return result


def _probe_customer_debtor_name(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Resolve a token against the unique `Order.debtor_name` set.

    Customers are stored as denormalised `debtor_name` on `orders`, so a complaint flow
    that asks the user "which customer?" may receive a free-text name (e.g. "Jayson",
    "IJM Land"). We treat any matching debtor as `entity_type=customer` and surface the
    debtor_name as the canonical code so downstream tools can pass it via `query`.
    """
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result

    # Tier 1 - exact case-insensitive match, plus a normalized exact so a
    # separator-stripped token ("MASTILEKLANGSDNBHD") still lands on Tier 1
    # rather than falling through to the substring scan.
    lowered = [t.lower() for t in tokens if t]
    name_to_token = {t.lower(): t for t in tokens if t}
    norm_to_token = _norm_token_map(tokens)
    exact_clauses = [func.lower(Order.debtor_name).in_(lowered)]
    if norm_to_token:
        exact_clauses.append(_ws_insensitive_lower(Order.debtor_name).in_(list(norm_to_token)))
    rows = (
        db.query(Order.debtor_name, Order.debtor_code, Customer.id)
        .outerjoin(Customer, func.lower(func.btrim(Customer.customer_name)) == func.lower(func.btrim(Order.debtor_name)))
        .filter(
            Order.deleted_at.is_(None),
            Order.debtor_name.isnot(None),
            or_(*exact_clauses),
        )
        .distinct()
        .all()
    )
    for debtor_name, debtor_code, customer_id in rows:
        token = name_to_token.get(str(debtor_name).lower()) or norm_to_token.get(
            _strip_all_ws(str(debtor_name)).lower()
        )
        if not token:
            continue
        if any(m.canonical_code == debtor_name for m in result[token]):
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="customer",
                canonical_code=debtor_name,
                uuid=str(customer_id) if customer_id else None,
                match_field="debtor_name",
                display={"debtor_name": debtor_name, "debtor_code": debtor_code, "source": "orders"},
            )
        )

    # Tier 2 fallback - partial / fuzzy ILIKE on debtor_name for any token still
    # unresolved. The debtor master is denormalised onto `orders`, so customers
    # without a `customers` row (e.g. "FIRA VENTURE ENTERPRISE (PROJECT-CASH)")
    # are only reachable via this scan. Also tolerates pluralised input
    # ("fira ventures" → "FIRA VENTURE …") by stripping a trailing 's'.
    unresolved = [t for t in tokens if not result[t]]
    for token in unresolved:
        # Guard: a 2-3 char token (e.g. "DO" meaning Delivery Order) is too
        # short to safely substring-match a debtor name without false hits
        # like "FREDONIA". Tier-1 exact match above still catches short
        # tokens that legitimately equal a full debtor_name.
        if len(token) < 4:
            continue
        variants: list[str] = [token]
        stripped = token.rstrip("s")
        if stripped and stripped != token and len(stripped) >= 4:
            variants.append(stripped)
        like_clauses = [_norm_contains(Order.debtor_name, v) for v in variants if v]
        if not like_clauses:
            continue
        _DEBTOR_FUZZY_LIMIT = 25
        rows = (
            db.query(Order.debtor_name, Order.debtor_code, Customer.id)
            .outerjoin(Customer, func.lower(func.btrim(Customer.customer_name)) == func.lower(func.btrim(Order.debtor_name)))
            .filter(
                Order.deleted_at.is_(None),
                Order.debtor_name.isnot(None),
                or_(*like_clauses),
            )
            .distinct()
            .limit(_DEBTOR_FUZZY_LIMIT + 1)
            .all()
        )
        truncated = len(rows) > _DEBTOR_FUZZY_LIMIT
        rows = rows[:_DEBTOR_FUZZY_LIMIT]
        for debtor_name, debtor_code, customer_id in rows:
            if any(m.canonical_code == debtor_name for m in result[token]):
                continue
            display = {"debtor_name": debtor_name, "debtor_code": debtor_code, "source": "orders"}
            if truncated:
                display["truncated_more_available"] = True
            result[token].append(
                ResolvedEntity(
                    entity_type="customer",
                    canonical_code=debtor_name,
                    uuid=str(customer_id) if customer_id else None,
                    match_field="debtor_name",
                    display=display,
                )
            )
    return result


def _transporter_name_clauses(token: str, *, include_code: bool) -> list:
    """Substring clauses for a transporter token across code / name / normalized_name."""
    clauses = [
        _norm_contains(Transporter.name, token),
        _norm_contains(Transporter.normalized_name, token),
    ]
    if include_code:
        clauses.append(_norm_contains(Transporter.code, token))
    return clauses


def _probe_transporter_freeword(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Free-text substring lookup against `Transporter.name` / `normalized_name`.

    The free-text debtor_name scan handles customer phrases like "fira ventures";
    this mirror covers transporter phrases like "gt delivery" or "suncrest" so
    a single `entities` bag can resolve transporter mentions without an explicit
    marker. Token length >= 4 to avoid false positives like "DO".
    """
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    for token in tokens:
        if not token or len(token) < 4:
            continue
        term = f"%{token}%"
        rows = (
            db.query(Transporter.id, Transporter.code, Transporter.name, Transporter.normalized_name)
            .filter(or_(*_transporter_name_clauses(token, include_code=True)))
            .limit(25)
            .all()
        )
        for tid, code, name, _norm in rows:
            if any(m.canonical_code == code for m in result[token]):
                continue
            result[token].append(
                ResolvedEntity(
                    entity_type="transporter",
                    canonical_code=code,
                    uuid=str(tid) if tid else None,
                    match_field="transporter_name",
                    match_tier="substring",
                    display={"code": code, "name": name},
                )
            )
    return result


def _probe_transporter(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Exact match on transporter code/name/normalized_name; ilike fallback for
    tokens that look like a transporter label (e.g. "suncrest", "asac")."""
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    lowered = [t.lower() for t in tokens if t]
    # Code, name and normalized_name all match dash/whitespace-insensitively.
    normalized_codes = [_strip_all_ws(t) for t in lowered]
    exact_clauses = [
        _ws_insensitive_lower(Transporter.code).in_(normalized_codes),
        func.lower(Transporter.name).in_(lowered),
        func.lower(Transporter.normalized_name).in_(lowered),
        _ws_insensitive_lower(Transporter.name).in_(normalized_codes),
        _ws_insensitive_lower(Transporter.normalized_name).in_(normalized_codes),
    ]
    rows = (
        db.query(Transporter.id, Transporter.code, Transporter.name, Transporter.normalized_name)
        .filter(or_(*exact_clauses))
        .all()
    )
    for tid, code, name, norm in rows:
        for token in tokens:
            tl = token.lower()
            tl_no_ws = _strip_all_ws(tl)
            code_no_ws = _strip_all_ws((code or "").lower())
            name_forms = {(name or "").lower(), (norm or "").lower()}
            name_forms |= {_strip_all_ws(f) for f in name_forms}
            name_hit = tl in name_forms or tl_no_ws in name_forms
            if tl_no_ws != code_no_ws and not name_hit:
                continue
            if any(m.canonical_code == code for m in result[token]):
                continue
            result[token].append(
                ResolvedEntity(
                    entity_type="transporter",
                    canonical_code=code,
                    uuid=str(tid) if tid else None,
                    match_field="transporter_code" if tl == (code or "").lower() else "transporter_name",
                    display={"code": code, "name": name},
                )
            )
    # Substring fallback for unresolved tokens.
    unresolved = [t for t in tokens if not result[t]]
    for token in unresolved:
        if len(token) < 4:
            continue
        rows = (
            db.query(Transporter.id, Transporter.code, Transporter.name, Transporter.normalized_name)
            .filter(or_(*_transporter_name_clauses(token, include_code=False)))
            .limit(25)
            .all()
        )
        for tid, code, name, _norm in rows:
            if any(m.canonical_code == code for m in result[token]):
                continue
            result[token].append(
                ResolvedEntity(
                    entity_type="transporter",
                    canonical_code=code,
                    uuid=str(tid) if tid else None,
                    match_field="transporter_name",
                    display={"code": code, "name": name},
                )
            )
    return result


def _region_overlap_clause(regions):
    """Shipment serves one of `regions` (packing list regions); None = no filter."""
    if regions is None:
        return true()
    return InboundShipment.regions.overlap(sorted(regions))


def _probe_inbound_shipment(
    db: Session, tokens: list[str], regions=None
) -> dict[str, list[ResolvedEntity]]:
    """Exact match across shipment_number / container / BOL / SO ref / invoice.

    `regions` (a contact's packing list regions) hides shipments outside them; None = all."""
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    # All the match fields are number-style identifiers - strip whitespace
    # both sides so 'SHP- 2026-001' matches 'SHP-2026-001' etc.
    normalized = [_strip_all_ws(t.lower()) for t in tokens]
    rows = (
        db.query(
            InboundShipment.id,
            InboundShipment.shipment_number,
            InboundShipment.shipping_container_number,
            InboundShipment.bill_of_lading_number,
            # `提单号` lands here (the SO field), not `bill_of_lading_number`, on an
            # upload made through the supplier-documents dialog (Q1 ruling) - a search
            # on the B/L alone missed every container uploaded that way (S3, review
            # round 1).
            InboundShipment.forwarder_order_ref,
            InboundShipment.invoice_number,
            InboundShipment.shipment_status,
            InboundShipment.estimated_arrival_date,
            InboundShipment.actual_arrival_date,
        )
        .filter(
            or_(
                _ws_insensitive_lower(InboundShipment.shipment_number).in_(normalized),
                _ws_insensitive_lower(InboundShipment.shipping_container_number).in_(normalized),
                _ws_insensitive_lower(InboundShipment.bill_of_lading_number).in_(normalized),
                _ws_insensitive_lower(InboundShipment.forwarder_order_ref).in_(normalized),
                _ws_insensitive_lower(InboundShipment.invoice_number).in_(normalized),
            ),
            InboundShipment.shipment_status != _DRAFT_SHIPMENT_STATUS,
            _region_overlap_clause(regions),
        )
        .all()
    )
    for row in rows:
        for token in tokens:
            tl_no_ws = _strip_all_ws(token.lower())
            match_field = None
            if row.shipment_number and _strip_all_ws(row.shipment_number.lower()) == tl_no_ws:
                match_field = "shipment_number"
            elif row.shipping_container_number and _strip_all_ws(row.shipping_container_number.lower()) == tl_no_ws:
                match_field = "shipping_container_number"
            elif row.bill_of_lading_number and _strip_all_ws(row.bill_of_lading_number.lower()) == tl_no_ws:
                match_field = "bill_of_lading_number"
            elif row.forwarder_order_ref and _strip_all_ws(row.forwarder_order_ref.lower()) == tl_no_ws:
                match_field = "forwarder_order_ref"
            elif row.invoice_number and _strip_all_ws(row.invoice_number.lower()) == tl_no_ws:
                match_field = "invoice_number"
            if not match_field:
                continue
            result[token].append(
                ResolvedEntity(
                    entity_type="inbound_shipment",
                    canonical_code=row.shipment_number,
                    uuid=str(row.id) if row.id else None,
                    match_field=match_field,
                    display={
                        "shipment_number": row.shipment_number,
                        "shipping_container_number": row.shipping_container_number,
                        "shipment_status": row.shipment_status,
                        "estimated_arrival_date": _iso(row.estimated_arrival_date),
                        "actual_arrival_date": _iso(row.actual_arrival_date),
                    },
                )
            )
    return result


def _probe_spo(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """R8/AC-E4: a number whose every line is retired must not resolve as a live
    entity, so `visible_line_clauses()` is a WHERE filter here, not a post-filter -
    a number with a visible sibling still resolves off that row's id, and a number
    with none is simply absent from `rows`, which the loop below already treats as
    "no match" for the token.
    """
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    norm_to_token = {_strip_all_ws(t.lower()): t for t in tokens}
    rows = (
        db.query(SPOAllocation.id, SPOAllocation.spo_number)
        .filter(
            _ws_insensitive_lower(SPOAllocation.spo_number).in_(list(norm_to_token.keys())),
            *spo_supply.visible_line_clauses(),
        )
        .distinct()
        .all()
    )
    for sid, spo_number in rows:
        token = norm_to_token.get(_strip_all_ws(str(spo_number or "").lower()))
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="spo_allocation",
                canonical_code=spo_number,
                uuid=str(sid) if sid else None,
                match_field="spo_number",
                display={"spo_number": spo_number},
            )
        )
    return result


def _probe_grn(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    norm_to_token = {_strip_all_ws(t.lower()): t for t in tokens}
    rows = (
        db.query(
            PickingHeader.id,
            PickingHeader.picking_number,
            PickingHeader.picking_date,
            PickingHeader.picking_status,
            PickingHeader.picking_type,
        )
        .filter(
            _ws_insensitive_lower(PickingHeader.picking_number).in_(list(norm_to_token.keys())),
            PickingHeader.picking_type == "goods_received",
        )
        .all()
    )
    for row in rows:
        token = norm_to_token.get(_strip_all_ws(str(row.picking_number).lower()))
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="grn",
                canonical_code=row.picking_number,
                uuid=str(row.id) if row.id else None,
                match_field="picking_number",
                display={
                    "grn_number": row.picking_number,
                    "grn_date": _iso(row.picking_date),
                    "grn_status": row.picking_status,
                },
            )
        )
    return result


def _probe_warehouse(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Exact match on `warehouse_code` ONLY, normalized by casefold + strip every
    non-alphanumeric character on both sides (owner ruling 8 Sep 2026,
    chatbot-warehouse-entity-and-last-in: "it is actually the bare code, so it should be
    exact match ... brw ib, brwib should map to brw-ib, but when we say brw, it means brw,
    not the rest"). No prefix and no fuzzy fan-out for this type - a token that is not an
    exact normalized code is a miss here, and `_prefix_probe_warehouse` /
    `_and_probe_warehouse` delegate back to this function rather than widen the match.
    Inactive warehouses still resolve; the caller decides what to show."""
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    norm_to_token = {_alnum_casefold(t): t for t in tokens if _alnum_casefold(t)}
    if not norm_to_token:
        return result
    rows = (
        db.query(Warehouse.id, Warehouse.warehouse_code, Warehouse.warehouse_name, Warehouse.location, Warehouse.is_active)
        .filter(_alnum_insensitive_lower(Warehouse.warehouse_code).in_(list(norm_to_token.keys())))
        .all()
    )
    for wid, code, name, location, is_active in rows:
        token = norm_to_token.get(_alnum_casefold(str(code)))
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="warehouse",
                canonical_code=code,
                uuid=str(wid) if wid else None,
                match_field="warehouse_code",
                display={
                    "warehouse_name": name,
                    "location": location,
                    "is_active": bool(is_active) if is_active is not None else True,
                },
            )
        )
    return result


def _probe_supplier(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    norm_to_token = {_strip_all_ws(t.lower()): t for t in tokens}
    rows = (
        db.query(
            Supplier.id,
            Supplier.supplier_code,
            Supplier.supplier_name,
            Supplier.contact_name,
            Supplier.email,
            Supplier.phone_number,
            Supplier.is_active,
        )
        .filter(_ws_insensitive_lower(Supplier.supplier_code).in_(list(norm_to_token.keys())))
        .all()
    )
    for sid, code, name, contact, email, phone, is_active in rows:
        token = norm_to_token.get(_strip_all_ws(str(code).lower()))
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="supplier",
                canonical_code=code,
                uuid=str(sid) if sid else None,
                match_field="supplier_code",
                display={
                    "supplier_name": name,
                    "contact_name": contact,
                    "email": email,
                    "phone_number": phone,
                    "is_active": bool(is_active) if is_active is not None else True,
                },
            )
        )
    return result


def _probe_promotion(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    return {t: [] for t in tokens}


def _probe_attachment(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Exact case-insensitive match on attachments.original_filename (without extension or with).

    Attachments have no business code; the filename and user-editable description are the
    only human handles. Tier-1 matches a token equal to the filename (e.g. "catalogue-2026.pdf").
    """
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    lowered = [t.lower() for t in tokens]
    name_to_token = {t.lower(): t for t in tokens}
    norm_to_token = _norm_token_map(tokens)
    exact_clauses = [func.lower(Attachment.original_filename).in_(lowered)]
    if norm_to_token:
        exact_clauses.append(
            _ws_insensitive_lower(Attachment.original_filename).in_(list(norm_to_token))
        )
    rows = (
        db.query(
            Attachment.id,
            Attachment.original_filename,
            Attachment.description,
            Attachment.mime_type,
            Attachment.full_directory_path,
            AttachmentType.type_name,
        )
        .outerjoin(AttachmentType, AttachmentType.id == Attachment.attachment_type_id)
        .filter(
            Attachment.is_deleted.is_(False),
            or_(*exact_clauses),
        )
        .all()
    )
    for aid, filename, description, mime, dir_path, type_name in rows:
        token = name_to_token.get(str(filename).lower()) or norm_to_token.get(
            _strip_all_ws(str(filename)).lower()
        )
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="attachment",
                canonical_code=filename,
                uuid=str(aid) if aid else None,
                match_field="original_filename",
                display={
                    "filename": filename,
                    "description": description,
                    "attachment_type": type_name,
                    "mime_type": mime,
                    "directory": dir_path,
                },
            )
        )
    return result


def _prefer_live_certificates(matches: list[Any]) -> list[Any]:
    """Drop expired certificates WHEN a live one answers the same token.

    The point is what happens downstream: a resolved uuid is handed straight to
    `crm_certificates_list`, so an expired row resolving first means the agent
    delivers a lapsed PDF. Where a live certificate exists for the same number
    (typically the same number approved under two schemes, one lapsed), the
    expired one is noise and is removed.

    It is deliberately NOT a hard filter. If EVERY match is expired, the expired
    match is still returned, flagged `is_expired`. Hiding it outright would make
    the agent answer "I cannot find that certificate" about a certificate that
    plainly exists - a worse answer than "found, but it expired on <date>", and
    the exact failure the tool description tells the agent to avoid.
    """
    live = [m for m in matches if not (m.display or {}).get("is_expired")]
    return live if live else matches


def _certificate_display(row: Any) -> dict[str, Any]:
    """Display payload for one certificate hit: identity plus DERIVED validity.

    The agent must be able to say "found but EXPIRED" without doing calendar
    arithmetic, so the state ships beside the date. Validity comes from the
    CURRENT revision only - a superseded window never makes a certificate read
    expired.
    """
    from app.services.certificate_service import derive_validity

    state, is_expired, days = derive_validity(row.valid_from, row.valid_until)
    return {
        "scheme": row.scheme,
        "certificate_number": row.certificate_number,
        "certifying_body": row.certifying_body,
        "status": row.status,
        "valid_from": _iso(row.valid_from),
        "valid_until": _iso(row.valid_until),
        "validity_state": state,
        "is_expired": is_expired,
        "days_until_expiry": days,
    }


def _certificate_columns(db: Session):
    """Base SELECT for both certificate probes: identity + current revision dates."""
    return (
        db.query(
            Certificate.id,
            Certificate.scheme,
            Certificate.certificate_number,
            Certificate.certifying_body,
            Certificate.status,
            CertificateRevision.valid_from,
            CertificateRevision.valid_until,
        )
        .outerjoin(
            CertificateRevision,
            CertificateRevision.id == Certificate.current_revision_id,
        )
    )


def _probe_certificate(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Exact match on the certificate number, whitespace- and dash-insensitive.

    Matched two ways so the user can type either half of the identity:
      * the number alone - "04124FC", "WCM PC 000321"
      * scheme + number - "PPS 0119", "PPS0119", "pps-0119"

    Both sides are normalized with the same `_strip_all_ws` / `_ws_insensitive_lower`
    pair that already makes `WC 8038` match `WC8038`, so no normalized column is
    needed.

    A number can exist under TWO schemes (`04124FC` is approved under both PPS
    and SPAN, with different expiries). Every match is returned; the caller flags
    the token ambiguous so the agent asks which scheme rather than answering from
    whichever row came back first.
    """
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    normalized = [_strip_all_ws(t.lower()) for t in tokens]
    norm_to_token = {n: t for n, t in zip(normalized, tokens) if n}
    if not norm_to_token:
        return result
    keys = list(norm_to_token.keys())
    number_norm = _ws_insensitive_lower(Certificate.certificate_number)
    identity_norm = _ws_insensitive_lower(
        Certificate.scheme + Certificate.certificate_number
    )
    rows = (
        _certificate_columns(db)
        .filter(or_(number_norm.in_(keys), identity_norm.in_(keys)))
        .all()
    )
    for row in rows:
        number_key = _strip_all_ws(str(row.certificate_number or "").lower())
        identity_key = _strip_all_ws(
            f"{row.scheme or ''}{row.certificate_number or ''}".lower()
        )
        token = norm_to_token.get(number_key)
        match_field = "certificate_number"
        if not token:
            token = norm_to_token.get(identity_key)
            match_field = "scheme_certificate_number"
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="certificate",
                canonical_code=row.certificate_number,
                uuid=str(row.id) if row.id else None,
                match_field=match_field,
                display=_certificate_display(row),
            )
        )
    return {token: _prefer_live_certificates(hits) for token, hits in result.items()}


def _probe_attachment_type(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Exact case-insensitive match on AttachmentType.code OR type_name.

    AttachmentType is a small reference table (BROCHURE / SPEC SHEET / DATASHEET /
    CATALOGUE / etc.). Free-text doc-class words like "catalogue", "brochure",
    "spec sheet" resolve to the canonical AttachmentType UUID, which downstream
    callers feed as `attachment_type_ids` / `attachment_type_id` to attachment
    list tools.
    """
    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    lowered = [t.lower() for t in tokens]
    exact_to_token = {t.lower(): t for t in tokens}
    norm_to_token = _norm_token_map(tokens)
    exact_clauses = [
        func.lower(AttachmentType.code).in_(lowered),
        func.lower(AttachmentType.type_name).in_(lowered),
    ]
    if norm_to_token:
        norm_keys = list(norm_to_token)
        exact_clauses.append(_ws_insensitive_lower(AttachmentType.code).in_(norm_keys))
        exact_clauses.append(_ws_insensitive_lower(AttachmentType.type_name).in_(norm_keys))
    rows = (
        db.query(
            AttachmentType.id,
            AttachmentType.code,
            AttachmentType.type_name,
            AttachmentType.description,
        )
        .filter(or_(*exact_clauses))
        .all()
    )

    def _token_for(value: str | None) -> str | None:
        if not value:
            return None
        return exact_to_token.get(value.lower()) or norm_to_token.get(_strip_all_ws(value).lower())

    for tid, code, type_name, description in rows:
        token = _token_for(code)
        match_field = "code"
        if not token:
            token = _token_for(type_name)
            match_field = "type_name"
        if not token:
            continue
        canonical = code or type_name
        result[token].append(
            ResolvedEntity(
                entity_type="attachment_type",
                canonical_code=canonical,
                uuid=str(tid) if tid else None,
                match_field=match_field,
                display={
                    "code": code,
                    "type_name": type_name,
                    "description": description,
                },
            )
        )
    return result


# --------------------------------------------------------------------------- #
# Tier 2 - prefix / substring probes (run only for tokens Tier 1 missed)
# --------------------------------------------------------------------------- #
# A Tier-2 probe takes a SINGLE token and returns a list of candidate entities.
# - Preference order: prefix match first, then substring.
# - Any token with >1 candidate is surfaced to the LLM as "ambiguous" so it asks
#   the user to pick, rather than silently guessing or failing with "no record".
PREFIX_LIMIT = 20


def _product_family_order():
    """The one order a product probe returns its rows in, whatever plan Postgres picks.

    With no ORDER BY the rows came back in heap order (an index scan over one
    company_id key walks row positions), so "srtwc286" listed SRTWC286-SH first on one
    run and SRTWC286-SH-NEW-P on the next, and a "1 2 3" family pick meant different
    products (deploy of d79b46c5). Code order is the family's own order: the base code
    is a prefix of its variants, so it sorts first. The id breaks a tie between two
    companies' copies of one code."""
    return (Product.product_code, Product.id)


def _prefix_probe_product(db: Session, token: str) -> list[ResolvedEntity]:
    # Strip whitespace from both sides so 'cgb9032b- new' matches 'CGB9032B-NEW'.
    norm_token = _strip_all_ws(token)
    prefix = f"{norm_token}%"
    substr = f"%{norm_token}%"
    code_norm = _ws_insensitive_lower(Product.product_code)
    rows = (
        db.query(Product.id, Product.product_code, Product.product_name, Product.is_active)
        .filter(code_norm.ilike(prefix), chat_searchable_products())
        .order_by(*_product_family_order())
        .limit(PREFIX_LIMIT)
        .all()
    )
    tier = "prefix"
    if not rows:
        rows = (
            db.query(Product.id, Product.product_code, Product.product_name, Product.is_active)
            .filter(code_norm.ilike(substr), chat_searchable_products())
            .order_by(*_product_family_order())
            .limit(PREFIX_LIMIT)
            .all()
        )
        tier = "substring"
    # Product resolution is CODE-ONLY by design: exact/prefix/substring probes
    # (and the trgm fallback) all match on product_code. Name/description
    # free-text matching was intentionally removed - a phrase like "basin" is
    # not a product identifier, so it must not resolve to a specific SKU.
    return [
        ResolvedEntity(
            entity_type="product",
            canonical_code=code,
            uuid=str(pid) if pid else None,
            match_field="product_code",
            match_tier=tier,
            display={"product_name": name, "is_active": bool(is_active)},
        )
        for pid, code, name, is_active in rows
    ]


def _prefix_probe_customer_order(db: Session, token: str) -> list[ResolvedEntity]:
    rows = (
        db.query(
            Order.id,
            Order.order_number,
            Order.debtor_name,
            Order.actual_delivery_date,
            OrderStatus.status_name,
        )
        .outerjoin(OrderStatus, OrderStatus.id == Order.order_status_id)
        .filter(_norm_prefix(Order.order_number, token), Order.deleted_at.is_(None))
        .limit(PREFIX_LIMIT)
        .all()
    )
    return [
        ResolvedEntity(
            entity_type="customer_order",
            canonical_code=row.order_number,
            uuid=str(row.id) if row.id else None,
            match_field="order_number",
            match_tier="prefix",
            display={
                "customer_name": row.debtor_name,
                "status": row.status_name,
                "actual_delivery_date": _iso(row.actual_delivery_date),
            },
        )
        for row in rows
    ]


def _prefix_probe_inbound_shipment(
    db: Session, token: str, regions=None
) -> list[ResolvedEntity]:
    rows = (
        db.query(
            InboundShipment.id,
            InboundShipment.shipment_number,
            InboundShipment.shipping_container_number,
            InboundShipment.shipment_status,
            InboundShipment.estimated_arrival_date,
        )
        .filter(
            or_(
                _norm_prefix(InboundShipment.shipment_number, token),
                _norm_prefix(InboundShipment.shipping_container_number, token),
                _norm_prefix(InboundShipment.bill_of_lading_number, token),
                # `提单号` lands here (the SO field), not `bill_of_lading_number`, on an
                # upload made through the supplier-documents dialog (S3, review round 1).
                _norm_prefix(InboundShipment.forwarder_order_ref, token),
                _norm_prefix(InboundShipment.invoice_number, token),
            ),
            InboundShipment.shipment_status != _DRAFT_SHIPMENT_STATUS,
            _region_overlap_clause(regions),
        )
        .limit(PREFIX_LIMIT)
        .all()
    )
    return [
        ResolvedEntity(
            entity_type="inbound_shipment",
            canonical_code=row.shipment_number,
            uuid=str(row.id) if row.id else None,
            match_field="shipment_number",
            match_tier="prefix",
            display={
                "shipment_number": row.shipment_number,
                "shipping_container_number": row.shipping_container_number,
                "shipment_status": row.shipment_status,
                "estimated_arrival_date": _iso(row.estimated_arrival_date),
            },
        )
        for row in rows
    ]


def _prefix_probe_customer(db: Session, token: str) -> list[ResolvedEntity]:
    rows = (
        db.query(Customer.id, Customer.customer_code, Customer.customer_name, Customer.phone_number)
        .filter(_norm_prefix(Customer.customer_code, token))
        .limit(PREFIX_LIMIT)
        .all()
    )
    return [
        ResolvedEntity(
            entity_type="customer",
            canonical_code=code,
            uuid=str(cid) if cid else None,
            match_field="customer_code",
            match_tier="prefix",
            display={"customer_name": name, "phone_number": phone},
        )
        for cid, code, name, phone in rows
    ]


def _prefix_probe_customer_debtor_name(db: Session, token: str) -> list[ResolvedEntity]:
    """Prefix → substring ILIKE on `Order.debtor_name` (distinct).

    Returns at most PREFIX_LIMIT distinct debtors. The caller treats >1 result as
    ambiguous and asks the user to pick.
    """
    if not token or len(token) < 3:
        return []
    rows = (
        db.query(Order.debtor_name, Order.debtor_code, Customer.id)
        .outerjoin(Customer, func.lower(func.btrim(Customer.customer_name)) == func.lower(func.btrim(Order.debtor_name)))
        .filter(
            Order.deleted_at.is_(None),
            Order.debtor_name.isnot(None),
            _norm_prefix(Order.debtor_name, token),
        )
        .distinct()
        .limit(PREFIX_LIMIT)
        .all()
    )
    tier = "prefix"
    if not rows:
        rows = (
            db.query(Order.debtor_name, Order.debtor_code, Customer.id)
            .outerjoin(Customer, func.lower(func.btrim(Customer.customer_name)) == func.lower(func.btrim(Order.debtor_name)))
            .filter(
                Order.deleted_at.is_(None),
                Order.debtor_name.isnot(None),
                _norm_contains(Order.debtor_name, token),
            )
            .distinct()
            .limit(PREFIX_LIMIT)
            .all()
        )
        tier = "substring"
    if not rows:
        # Per-word AND fallback - handles typos / dropped letters in multi-word
        # debtor phrases. Token "Delux home centre" against "DELUXE HOME CENTRE
        # SDN BHD": full-string substring miss (extra 'E' in DELUXE breaks the
        # contiguous phrase) but every word still appears individually. AND
        # across words ≥3 chars on debtor_name.
        words = [w for w in token.split() if len(w) >= 3]
        if len(words) >= 2:
            q = (
                db.query(Order.debtor_name, Order.debtor_code, Customer.id)
                .outerjoin(Customer, func.lower(func.btrim(Customer.customer_name)) == func.lower(func.btrim(Order.debtor_name)))
                .filter(Order.deleted_at.is_(None), Order.debtor_name.isnot(None))
            )
            for w in words:
                q = q.filter(_norm_contains(Order.debtor_name, w))
            rows = q.distinct().limit(PREFIX_LIMIT).all()
            tier = "word"
    seen: set[str] = set()
    out: list[ResolvedEntity] = []
    for debtor_name, debtor_code, customer_id in rows:
        key = (debtor_name or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            ResolvedEntity(
                entity_type="customer",
                canonical_code=debtor_name,
                uuid=str(customer_id) if customer_id else None,
                match_field="debtor_name",
                match_tier=tier,
                display={"debtor_name": debtor_name, "debtor_code": debtor_code, "source": "orders"},
            )
        )
    return out


def _prefix_probe_warehouse(db: Session, token: str) -> list[ResolvedEntity]:
    """Not actually a prefix probe: the owner ruling (8 Sep 2026,
    chatbot-warehouse-entity-and-last-in) bans prefix fan-out for warehouse ("brw" must
    never surface `BRW-IB`/`BRW-IR`), so this Tier-2 slot delegates to the same exact
    normalized-code match as `_probe_warehouse` and returns at most the one row a token
    names outright."""
    return _probe_warehouse(db, [token]).get(token, [])


def _prefix_probe_supplier(db: Session, token: str) -> list[ResolvedEntity]:
    rows = (
        db.query(Supplier.id, Supplier.supplier_code, Supplier.supplier_name, Supplier.is_active)
        .filter(_norm_prefix(Supplier.supplier_code, token))
        .limit(PREFIX_LIMIT)
        .all()
    )
    return [
        ResolvedEntity(
            entity_type="supplier",
            canonical_code=code,
            uuid=str(sid) if sid else None,
            match_field="supplier_code",
            match_tier="prefix",
            display={"supplier_name": name, "is_active": bool(is_active)},
        )
        for sid, code, name, is_active in rows
    ]


def _prefix_probe_spo(db: Session, token: str) -> list[ResolvedEntity]:
    """R8/AC-E4: same WHERE-filter reasoning as `_probe_spo` above."""
    rows = (
        db.query(SPOAllocation.id, SPOAllocation.spo_number)
        .filter(
            _norm_prefix(SPOAllocation.spo_number, token),
            *spo_supply.visible_line_clauses(),
        )
        .distinct()
        .limit(PREFIX_LIMIT)
        .all()
    )
    return [
        ResolvedEntity(
            entity_type="spo_allocation",
            canonical_code=spo,
            uuid=str(sid) if sid else None,
            match_field="spo_number",
            match_tier="prefix",
            display={"spo_number": spo},
        )
        for sid, spo in rows
    ]


def _prefix_probe_grn(db: Session, token: str) -> list[ResolvedEntity]:
    rows = (
        db.query(
            PickingHeader.id,
            PickingHeader.picking_number,
            PickingHeader.picking_date,
            PickingHeader.picking_status,
        )
        .filter(
            _norm_prefix(PickingHeader.picking_number, token),
            PickingHeader.picking_type == "goods_received",
        )
        .limit(PREFIX_LIMIT)
        .all()
    )
    return [
        ResolvedEntity(
            entity_type="grn",
            canonical_code=row.picking_number,
            uuid=str(row.id) if row.id else None,
            match_field="picking_number",
            match_tier="prefix",
            display={
                "grn_number": row.picking_number,
                "grn_date": _iso(row.picking_date),
                "grn_status": row.picking_status,
            },
        )
        for row in rows
    ]


def _prefix_probe_transporter(db: Session, token: str) -> list[ResolvedEntity]:
    """Prefix → substring ILIKE on `Transporter.code` / `name` / `normalized_name`.

    Lets a free-text chunk like "gt delivery" / "suncrest" resolve to a transporter
    without needing an explicit marker word in the user's phrasing. Tier-2 keeps
    short noise tokens out via the >=3 length guard.
    """
    if not token or len(token) < 3:
        return []
    rows = (
        db.query(Transporter.id, Transporter.code, Transporter.name, Transporter.normalized_name)
        .filter(
            or_(
                _norm_prefix(Transporter.code, token),
                _norm_prefix(Transporter.name, token),
                _norm_prefix(Transporter.normalized_name, token),
            )
        )
        .limit(PREFIX_LIMIT)
        .all()
    )
    tier = "prefix"
    if not rows:
        rows = (
            db.query(Transporter.id, Transporter.code, Transporter.name, Transporter.normalized_name)
            .filter(or_(*_transporter_name_clauses(token, include_code=True)))
            .limit(PREFIX_LIMIT)
            .all()
        )
        tier = "substring"
    seen: set[str] = set()
    out: list[ResolvedEntity] = []
    for tid, code, name, _norm in rows:
        key = (code or name or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            ResolvedEntity(
                entity_type="transporter",
                canonical_code=code,
                uuid=str(tid) if tid else None,
                match_field="transporter_name",
                match_tier=tier,
                display={"code": code, "name": name},
            )
        )
    return out


def _prefix_probe_promotion(db: Session, token: str) -> list[ResolvedEntity]:
    """Prefix → substring ILIKE on Promotion.description.

    Phrase like "kitchen sink" should hit BOTH descriptions that start with it
    ("Kitchen Sink Promotion") and descriptions that contain it elsewhere
    ("Sorento Kitchen Sink Promo"). Prefix-only would miss the latter.
    """
    if not token or len(token) < 3:
        return []
    rows = (
        db.query(Promotion.id, Promotion.description, Promotion.is_active)
        .filter(_norm_prefix(Promotion.description, token))
        .limit(PREFIX_LIMIT)
        .all()
    )
    tier = "prefix"
    if not rows:
        rows = (
            db.query(Promotion.id, Promotion.description, Promotion.is_active)
            .filter(_norm_contains(Promotion.description, token))
            .limit(PREFIX_LIMIT)
            .all()
        )
        tier = "substring"
    seen: set[str] = set()
    out: list[ResolvedEntity] = []
    # Merge: also include substring hits not already captured by prefix.
    if tier == "prefix" and rows:
        substring_rows = (
            db.query(Promotion.id, Promotion.description, Promotion.is_active)
            .filter(_norm_contains(Promotion.description, token))
            .filter(~_norm_prefix(Promotion.description, token))
            .limit(PREFIX_LIMIT)
            .all()
        )
        rows = list(rows) + list(substring_rows)
    # Multi-word union (always runs alongside contiguous ILIKE when the token
    # has 2+ words; results merge + dedupe). Contiguous ILIKE misses
    # descriptions where the user's words are separated by other tokens
    # (e.g. token "sorento kitchen sink" vs description
    # "SORENTO NEW ARRIVAL KITCHEN SINK ..."). Require every word (>=2 chars)
    # to appear anywhere in Promotion.description.
    words = [w for w in token.split() if len(w) >= 2]
    if len(words) >= 2:
        word_query = db.query(Promotion.id, Promotion.description, Promotion.is_active)
        for w in words:
            word_query = word_query.filter(_norm_contains(Promotion.description, w))
        rows = list(rows) + list(word_query.limit(PREFIX_LIMIT).all())
    for pid, description, is_active in rows:
        key = str(pid)
        if key in seen:
            continue
        seen.add(key)
        is_substring_match = description and not str(description).lower().startswith(token.lower())
        out.append(
            ResolvedEntity(
                entity_type="promotion",
                canonical_code=str(pid),
                uuid=str(pid),
                match_field="description",
                match_tier="substring" if is_substring_match else "prefix",
                display={"description": description, "is_active": bool(is_active)},
            )
        )
    return out[: PREFIX_LIMIT * 2]


def _prefix_probe_form(db: Session, token: str) -> list[ResolvedEntity]:
    """Prefix → substring ILIKE on Form.code / Form.name / Form.purpose, with
    multi-token AND fallback.

    Forms have three searchable text columns. Match code first (highest signal),
    then name, then purpose (longest text, lowest signal). Each column tries
    prefix first, then substring. For multi-word inputs ("renovation form",
    "sponsorship request"), a final AND-tokenized pass requires every token to
    appear somewhere across code/name/purpose - covers descriptive phrasings
    where no single column carries the literal phrase.
    """
    if not token or len(token) < 3:
        return []
    from sqlalchemy import or_, and_
    fields = (
        (Form.code, "form_code"),
        (Form.name, "form_name"),
        (Form.purpose, "purpose"),
    )
    seen: set[str] = set()
    out: list[ResolvedEntity] = []

    def _append(fid, code, name, purpose, is_active, form_type, label, tier):
        key = str(fid)
        if key in seen:
            return
        seen.add(key)
        out.append(
            ResolvedEntity(
                entity_type="form",
                canonical_code=code,
                uuid=str(fid) if fid else None,
                match_field=label,
                match_tier=tier,
                display={
                    "form_code": code,
                    "form_name": name,
                    "form_type": form_type,
                    "is_active": bool(is_active),
                },
            )
        )

    for col, label in fields:
        rows = (
            db.query(Form.id, Form.code, Form.name, Form.purpose, Form.is_active, Form.form_type)
            .filter(_norm_prefix(col, token))
            .limit(PREFIX_LIMIT)
            .all()
        )
        substring_rows = (
            db.query(Form.id, Form.code, Form.name, Form.purpose, Form.is_active, Form.form_type)
            .filter(_norm_contains(col, token))
            .filter(~_norm_prefix(col, token))
            .limit(PREFIX_LIMIT)
            .all()
        )
        for fid, code, name, purpose, is_active, form_type in list(rows) + list(substring_rows):
            col_value = {"form_code": code, "form_name": name, "purpose": purpose}[label]
            tier = (
                "substring"
                if col_value and not str(col_value).lower().startswith(token.lower())
                else "prefix"
            )
            _append(fid, code, name, purpose, is_active, form_type, label, tier)

    # Multi-token AND fallback: each token must appear in code OR name OR purpose.
    tokens = [t for t in token.lower().split() if len(t) >= 3]
    if len(tokens) >= 2:
        token_conds = [
            or_(
                _norm_contains(Form.code, t),
                _norm_contains(Form.name, t),
                _norm_contains(Form.purpose, t),
            )
            for t in tokens
        ]
        and_rows = (
            db.query(Form.id, Form.code, Form.name, Form.purpose, Form.is_active, Form.form_type)
            .filter(and_(*token_conds))
            .limit(PREFIX_LIMIT)
            .all()
        )
        for fid, code, name, purpose, is_active, form_type in and_rows:
            _append(fid, code, name, purpose, is_active, form_type, "form_name", "tokenized")

    return out[: PREFIX_LIMIT * 2]


def _prefix_probe_attachment(db: Session, token: str) -> list[ResolvedEntity]:
    """Prefix → substring ILIKE on filename, description, attachment_type.type_name.

    Free-text doc references like "catalogue", "price list 2026", "spec sheet kitchen"
    resolve to attachment UUIDs. Excludes soft-deleted rows.
    """
    if not token or len(token) < 3:
        return []
    base = (
        db.query(
            Attachment.id,
            Attachment.original_filename,
            Attachment.description,
            Attachment.mime_type,
            Attachment.full_directory_path,
            AttachmentType.type_name,
        )
        .outerjoin(AttachmentType, AttachmentType.id == Attachment.attachment_type_id)
        .filter(Attachment.is_deleted.is_(False))
    )
    prefix_rows = (
        base.filter(
            or_(
                _norm_prefix(Attachment.original_filename, token),
                _norm_prefix(Attachment.description, token),
                _norm_prefix(AttachmentType.type_name, token),
            )
        )
        .limit(PREFIX_LIMIT)
        .all()
    )
    substring_rows = (
        base.filter(
            or_(
                _norm_contains(Attachment.original_filename, token),
                _norm_contains(Attachment.description, token),
                _norm_contains(AttachmentType.type_name, token),
            )
        )
        .limit(PREFIX_LIMIT)
        .all()
    )
    # Multi-word union (always runs alongside the contiguous prefix/substring
    # queries when the token has 2+ words; results are merged + deduped, not
    # gated on the contiguous result being empty). Contiguous ILIKE misses
    # filenames where the user's words are separated by other tokens
    # (e.g. token "sorento kitchen sink" vs filename
    # "SORENTO NEW ARRIVAL KITCHEN SINK_..."). Split on whitespace and require
    # every word (>=2 chars) to appear in any of the searched columns.
    # AND across words, OR across columns per word.
    words = [w for w in token.split() if len(w) >= 2]
    word_and_rows: list = []
    if len(words) >= 2:
        word_query = base
        for w in words:
            word_query = word_query.filter(
                or_(
                    _norm_contains(Attachment.original_filename, w),
                    _norm_contains(Attachment.description, w),
                    _norm_contains(AttachmentType.type_name, w),
                )
            )
        word_and_rows = word_query.limit(PREFIX_LIMIT).all()
    seen: set[str] = set()
    out: list[ResolvedEntity] = []
    for aid, filename, description, mime, dir_path, type_name in list(prefix_rows) + list(substring_rows) + list(word_and_rows):
        key = str(aid)
        if key in seen:
            continue
        seen.add(key)
        name_l = (filename or "").lower()
        desc_l = (description or "").lower()
        type_l = (type_name or "").lower()
        tier = (
            "prefix"
            if name_l.startswith(token.lower()) or desc_l.startswith(token.lower()) or type_l.startswith(token.lower())
            else "substring"
        )
        out.append(
            ResolvedEntity(
                entity_type="attachment",
                canonical_code=filename,
                uuid=key,
                match_field="original_filename",
                match_tier=tier,
                display={
                    "filename": filename,
                    "description": description,
                    "attachment_type": type_name,
                    "mime_type": mime,
                    "directory": dir_path,
                },
            )
        )
    return out[: PREFIX_LIMIT * 2]


def _prefix_probe_attachment_type(db: Session, token: str) -> list[ResolvedEntity]:
    """Prefix → substring → per-word ILIKE on AttachmentType.code / type_name / description.

    Handles partial / fuzzy doc-class words (e.g. token "cataloge" → "catalogue",
    "broch" → "Brochure"). Three-stage matching:
      1. Contiguous prefix ILIKE on code OR type_name.
      2. Contiguous substring ILIKE on code OR type_name OR description.
      3. Per-word substring ILIKE - for every word ≥4 chars in the token,
         match rows whose code / type_name / description contains it. Lets a
         loose user phrase like "technical drawing" resolve to
         "Technical Specifications" (one shared word is enough - caller already
         narrowed to attachment_type so semantic drift risk is bounded).
    Returns up to PREFIX_LIMIT candidates so ambiguous labels surface for LLM
    disambiguation.
    """
    if not token or len(token) < 2:
        return []
    base = db.query(
        AttachmentType.id,
        AttachmentType.code,
        AttachmentType.type_name,
        AttachmentType.description,
    )
    prefix_rows = (
        base.filter(
            or_(
                _norm_prefix(AttachmentType.code, token),
                _norm_prefix(AttachmentType.type_name, token),
            )
        )
        .limit(PREFIX_LIMIT)
        .all()
    )
    substring_rows = (
        base.filter(
            or_(
                _norm_contains(AttachmentType.code, token),
                _norm_contains(AttachmentType.type_name, token),
                _norm_contains(AttachmentType.description, token),
            )
        )
        .limit(PREFIX_LIMIT)
        .all()
    )
    # Per-word fallback for multi-word phrases (e.g. "technical drawing" with
    # no contiguous match against "Technical Specifications"). OR across words
    # so a single shared word is enough - attachment_type was explicitly hinted
    # by the caller, so loose matching is safer here than for general entities.
    word_rows: list = []
    words = [w for w in token.split() if len(w) >= 4]
    if words:
        word_clauses = []
        for w in words:
            word_clauses.extend(
                [
                    _norm_contains(AttachmentType.code, w),
                    _norm_contains(AttachmentType.type_name, w),
                    _norm_contains(AttachmentType.description, w),
                ]
            )
        word_rows = base.filter(or_(*word_clauses)).limit(PREFIX_LIMIT).all()

    seen: set[str] = set()
    out: list[ResolvedEntity] = []
    token_lower = token.lower()
    token_word_set = {w.lower() for w in words}
    for tid, code, type_name, description in (
        list(prefix_rows) + list(substring_rows) + list(word_rows)
    ):
        key = str(tid)
        if key in seen:
            continue
        seen.add(key)
        code_l = (code or "").lower()
        type_l = (type_name or "").lower()
        desc_l = (description or "").lower()
        if code_l.startswith(token_lower) or type_l.startswith(token_lower):
            tier = "prefix"
            match_field = "code" if code_l.startswith(token_lower) else "type_name"
        elif token_lower in code_l or token_lower in type_l or token_lower in desc_l:
            tier = "substring"
            match_field = "type_name"
        else:
            tier = "word"
            # Surface which token word actually hit so the agent can explain
            # the loose match.
            hit_word = next(
                (w for w in token_word_set if w in code_l or w in type_l or w in desc_l),
                "",
            )
            match_field = f"word:{hit_word}" if hit_word else "type_name"
        out.append(
            ResolvedEntity(
                entity_type="attachment_type",
                canonical_code=code or type_name,
                uuid=key,
                match_field=match_field,
                match_tier=tier,
                display={
                    "code": code,
                    "type_name": type_name,
                    "description": description,
                },
            )
        )
    return out[:PREFIX_LIMIT]


def _product_attachment_type_ids(db: Session) -> frozenset[str]:
    """AttachmentType ids a PRODUCT actually carries a file of, via `product_attachments`.

    Chatbot pass 5, item 1 (H78/AC-827, production regression against #713, migration 485's
    `attachment_types` seed): "photo" Tier-2 substring-matches BOTH "Product Photos" and
    "Shipment Line Photo" (`entity_type='inbound_shipment_line'` ALWAYS -
    `app/services/scm/shipment_line_photos.py`, never on a product) - an internal SCM
    document type that happens to share the substring "photo". `gate.py`'s own
    non-product ambiguity handling (`run_gate`, the OR-mode `non_products` branch)
    has no per-type narrowing, so the two collided and the customer got a did-you-mean
    for a document type they never asked about, or a silent wrong pick.

    **A product's own file is linked via `product_attachments` (`product_id` ->
    `attachment_id`), never via `attachments.entity_type`.** Measured against the local
    prod-copy database, read-only, review of this item's first cut: `attachments.entity_type`
    distribution is 4302 NULL / 77 `dealer_kit_asset` / 41 `stock_list` / 9
    `supplier_stock_list` / 1 `project` - ZERO rows carry `entity_type='product'`, so the
    first cut's own filter matched nothing and the frozenset it returned was EMPTY in
    production, leaving "photo" exactly as ambiguous as before the fix. Joining through
    `product_attachments` instead returns the real four types product files use today
    (2108 Product Photos / 1033 Technical Specifications / 569 Certification / 1
    Promotion) - measured, not invented, so a type stays a candidate exactly when a
    product photo / document of that type genuinely exists, no hardcoded denylist of
    codes a future internal type could silently miss.
    """
    rows = (
        db.query(Attachment.attachment_type_id)
        .join(ProductAttachment, ProductAttachment.attachment_id == Attachment.id)
        .filter(Attachment.attachment_type_id.isnot(None))
        .distinct()
        .all()
    )
    return frozenset(str(r[0]) for r in rows if r[0])


def _prefix_probe_certificate(db: Session, token: str) -> list[ResolvedEntity]:
    """Prefix → substring on the certificate number and on scheme + number.

    Catches a partial the user half-remembers ("04124", "WCM PC") and the
    scheme-qualified form ("PPS 041"). Both sides stay dash / whitespace
    insensitive, so "pps-041" reaches "PPS 04124FC". Certifying body and title
    are deliberately NOT probed here - loose prose is tier 3's job, and matching
    "IKRAM" would return every IKRAM certificate as an ambiguous blob.
    """
    norm_token = _strip_all_ws(token)
    if not norm_token or len(norm_token) < 3:
        return []
    prefix = f"{norm_token}%"
    substr = f"%{norm_token}%"
    number_norm = _ws_insensitive_lower(Certificate.certificate_number)
    identity_norm = _ws_insensitive_lower(
        Certificate.scheme + Certificate.certificate_number
    )
    rows = (
        _certificate_columns(db)
        .filter(or_(number_norm.ilike(prefix), identity_norm.ilike(prefix)))
        .limit(PREFIX_LIMIT)
        .all()
    )
    tier = "prefix"
    if not rows:
        rows = (
            _certificate_columns(db)
            .filter(or_(number_norm.ilike(substr), identity_norm.ilike(substr)))
            .limit(PREFIX_LIMIT)
            .all()
        )
        tier = "substring"
    out: list[ResolvedEntity] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.id)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ResolvedEntity(
                entity_type="certificate",
                canonical_code=row.certificate_number,
                uuid=key,
                match_field="certificate_number",
                match_tier=tier,
                display=_certificate_display(row),
            )
        )
    return _prefer_live_certificates(out)


# Tier-2 probes paired with the entity_type(s) they produce, so callers can opt
# out of probes that can't return anything they accept.
_TIER2_PROBES: tuple[tuple[Callable[[Session, str], list[ResolvedEntity]], frozenset[str]], ...] = (
    (_prefix_probe_product, frozenset({"product"})),
    (_prefix_probe_customer_order, frozenset({"customer_order"})),
    (_prefix_probe_inbound_shipment, frozenset({"inbound_shipment"})),
    (_prefix_probe_customer, frozenset({"customer"})),
    (_prefix_probe_customer_debtor_name, frozenset({"customer"})),
    (_prefix_probe_transporter, frozenset({"transporter"})),
    (_prefix_probe_warehouse, frozenset({"warehouse"})),
    (_prefix_probe_supplier, frozenset({"supplier"})),
    (_prefix_probe_spo, frozenset({"spo_allocation"})),
    (_prefix_probe_grn, frozenset({"grn"})),
    (_prefix_probe_promotion, frozenset({"promotion"})),
    (_prefix_probe_form, frozenset({"form"})),
    (_prefix_probe_attachment, frozenset({"attachment"})),
    (_prefix_probe_attachment_type, frozenset({"attachment_type"})),
    (_prefix_probe_certificate, frozenset({"certificate"})),
)


def _tier2_fuzzy_lookup(
    db: Session,
    token: str,
    allowed_entity_types: Optional[frozenset[str]] = None,
    regions=None,
) -> list[ResolvedEntity]:
    """Run Tier-2 prefix probes for a single token and return combined candidates.

    When `allowed_entity_types` is provided, probes whose output type is not in the set
    are skipped entirely - keeps per-tool resolution lean.
    """
    combined: list[ResolvedEntity] = []
    for probe, produces in _TIER2_PROBES:
        if allowed_entity_types is not None and produces.isdisjoint(allowed_entity_types):
            continue
        try:
            combined.extend(_memo_tier2(probe, db, token, regions))
        except Exception:
            logger.exception("Tier-2 probe %s failed for token=%s", probe.__name__, token)
    return combined


# --------------------------------------------------------------------------- #
# Tier 3 - embedding vector fallback (last resort)
# --------------------------------------------------------------------------- #
# Source types in embedding_chunks that correspond to primary business entities.
# Child tables (order_line, picking_line, ...) are excluded: they'd match on noise.
_EMBEDDING_SOURCE_TYPES: dict[str, str] = {
    "product": "product",
    "order": "customer_order",
    "inbound_shipment": "inbound_shipment",
    "spo_allocation": "spo_allocation",
    "picking_header": "grn",
    "promotion": "promotion",
    # Customer master rows + synthetic `debtor:<code>` chunks seeded from
    # distinct orders.debtor_code with no master row. Lets tokens like
    # "fira ventures" resolve to entity_type=customer via vector similarity
    # against the seeded debtor chunk text.
    "customer": "customer",
    # Transporter master (code + name) so "search DO by transporter X"
    # resolves to entity_type=transporter via vector similarity.
    "transporter": "transporter",
    "attachment": "attachment",
    "form": "form",
    # Certificate register rows (number + scheme + body + issuer + title +
    # covered product codes). Lets prose like "water fitting approval for angle
    # valves" resolve to a certificate. Dates are NOT embedded - validity is
    # answered by SQL.
    "certificate": "certificate",
}
EMBEDDING_MIN_SIMILARITY = 0.80
EMBEDDING_CONFIDENCE_GAP = 0.05


def _chat_visible_product_ids(db: Session, product_ids: Iterable[str]) -> set[str]:
    """The subset of `product_ids` the chatbot may answer with.

    The exact / prefix / AND probes apply `chat_searchable_products()` inline.
    The trigram and embedding tiers read raw SQL over tables the predicate does
    not join, so they re-check their product hits here through the ORM - one
    bounded IN query, company-scoped like every other product read - instead of
    each carrying a hand-written copy of the rule.
    """
    ids = [str(p) for p in product_ids if p]
    if not ids:
        return set()
    return {
        str(pid)
        for (pid,) in db.query(Product.id)
        .filter(Product.id.in_(ids), chat_searchable_products())
        .all()
    }


def _tier3_embedding_lookup(
    db: Session,
    token: str,
    allowed_entity_types: Optional[frozenset[str]] = None,
) -> list[ResolvedEntity]:
    """Vector search over embedding_chunks for primary entities. Returns at most one
    confident match, or an empty list when ambiguous / below threshold."""
    # Local imports: these modules pull in heavy deps; we only want to pay the cost
    # on the last-resort path.
    try:
        from app.services.embedding_worker import _embed_text_chunks
    except Exception:
        logger.exception("Tier-3 embedding worker import failed; skipping")
        return []

    try:
        query_vec = _memo_embedding(token, _embed_text_chunks)
    except Exception:
        logger.exception("Tier-3 query embedding failed for token=%s", token)
        return []

    if allowed_entity_types is None:
        allowed_types = tuple(_EMBEDDING_SOURCE_TYPES.keys())
    else:
        allowed_types = tuple(
            src for src, ent in _EMBEDDING_SOURCE_TYPES.items() if ent in allowed_entity_types
        )
        if not allowed_types:
            return []
    sql = text(
        """
        SELECT ec.source_type, ec.source_id, ed.source_key,
               1 - (ec.embedding <=> CAST(:vec AS vector)) AS similarity
        FROM embedding_chunks ec
        JOIN embedding_documents ed ON ed.source_type = ec.source_type AND ed.source_id = ec.source_id
        WHERE ec.is_current = TRUE
          AND ec.source_type = ANY(:types)
        ORDER BY ec.embedding <=> CAST(:vec AS vector)
        LIMIT 5
        """
    )
    try:
        # SAVEPOINT: this lookup is best-effort, and a failed statement
        # otherwise leaves the whole transaction aborted - every later
        # query in the request would die on InFailedSqlTransaction.
        with db.begin_nested():
            rows = db.execute(sql, {"vec": list(query_vec), "types": list(allowed_types)}).all()
    except Exception:
        logger.exception("Tier-3 vector query failed for token=%s", token)
        return []

    if not rows:
        return []

    top = rows[0]
    top_sim = float(top.similarity or 0.0)
    if top_sim < EMBEDDING_MIN_SIMILARITY:
        return []
    second_sim = float(rows[1].similarity) if len(rows) > 1 else 0.0
    if (top_sim - second_sim) < EMBEDDING_CONFIDENCE_GAP:
        return []

    entity_type = _EMBEDDING_SOURCE_TYPES.get(str(top.source_type))
    if not entity_type:
        return []
    if entity_type == "product" and not _chat_visible_product_ids(db, [top.source_id]):
        # A hidden placeholder is not an answer on the semantic tier either.
        return []
    canonical_code = top.source_key or str(top.source_id)
    return [
        ResolvedEntity(
            entity_type=entity_type,
            canonical_code=canonical_code,
            uuid=str(top.source_id) if top.source_id else None,
            match_field=f"embedding:{top.source_type}",
            match_tier="embedding",
            similarity=top_sim,
            display={"semantic_match": True},
        )
    ]


# --------------------------------------------------------------------------- #
# Tier 2.5 - pg_trgm typo-tolerant SQL match
# --------------------------------------------------------------------------- #
# Substring ILIKE (Tier-2) misses on typos / missing characters; embedding RAG
# (Tier-3) tolerates them but ranks noisily on short codes. pg_trgm sits in the
# middle: deterministic similarity-based SQL using GIN trigram indexes
# (migration 169 covers products.{product_code,product_name,description},
# orders.{order_number,debtor_name,debtor_code}, customers.{customer_code,
# customer_name}). Catches "sind entrprise" → "SVIND ENTERPRISE", "SRWC8088" →
# "SRTWC8088", etc.
TRGM_THRESHOLD = 0.25
TRGM_LIMIT = 15

# Substitution floor for the data-miss neighbour helper (§3.3 / §5.4). Higher than
# the TRGM_THRESHOLD recall floor (0.25): a barely-related in-stock product is a
# business error, so if the nearest DATA-BEARING neighbour is below this, we return
# nothing and n8n says "no similar products with stock". Tunable - adjust on replay.
SUGGEST_FLOOR = 0.40

# `resolve()`'s own per-token `alternatives` floor (a token that matched NOTHING at
# all, read by the ENTITY-miss path - `miss_suggest.build_suggest_offer`'s D1 arm, R-c,
# owner hand pass 6, 17 Sep 2026) - its OWN constant rather than `SUGGEST_FLOOR` above,
# because that one gates a DIFFERENT caller (the data-miss substitute-product helper)
# with pinned test fixtures at similarities between the two floors
# (`tests/test_suggest_neighbours.py::test_ac_n4_below_floor_neighbours_yield_empty`,
# ~0.31); sharing one constant would have moved that caller's floor as a side effect of
# tuning this one. Measured, not guessed: "srttwc286" -> SRTWC286-SH scored 0.62
# (already above 0.40), "strwc286" -> SRTWC286-SH scored 0.33 - a real typo the old
# 0.40 floor silently dropped.
ENTITY_MISS_SUGGEST_FLOOR = 0.30

# Entity types `_trgm_lookup` can probe. Used as the default scope for resolve
# "did you mean" alternatives when the caller passed no entity-type whitelist.
_ALL_TRGM_TYPES = frozenset({"product", "customer", "customer_order", "promotion", "transporter"})
# Max fuzzy alternatives surfaced per unresolved token (keep the suggestion tight).
_ALTERNATIVES_CAP = 5


def _trgm_lookup(
    db: Session,
    phrase: str,
    allowed_entity_types: frozenset[str],
) -> list[ResolvedEntity]:
    """Run pg_trgm similarity probes across allowed entity types' name+code columns.

    Returns up to `TRGM_LIMIT` hits per entity type sorted by similarity desc,
    filtered by `>= TRGM_THRESHOLD`. Uses indexed columns where available.
    """
    phrase = (phrase or "").strip()
    if not phrase or len(phrase) < 3:
        return []
    # Every probe below scores the dash/whitespace-STRIPPED form of both sides
    # (§3a), never the raw column. n8n strips separators from every token it
    # sends, so a raw `similarity()` compares a stripped query against a column
    # that kept its hyphen and collapses - "STRWC286SH" scored 0.50 against the
    # WRONG family while the dash-kept spelling scored 0.643 against the right
    # one.
    #
    # Normalized REPLACES raw rather than OR-ing beside it. Keeping both is what
    # `_norm_prefix` does, but there the raw side is a cheap ILIKE; here each
    # extra operand is a second GIN bitmap plus its recheck, and measured
    # end-to-end over ten phrases an OR of both cost 2.6x (73ms -> 195ms median,
    # p90 95ms -> 230ms) for ~15% more hits. Nothing is lost: separators carry no
    # meaning in these identifiers - that is the premise of §3a - so a token that
    # matched raw matches its stripped form too.
    norm = _strip_all_ws(phrase).lower() or phrase.lower()
    out: list[ResolvedEntity] = []

    if "product" in allowed_entity_types:
        try:
            # Make the code's TRGM_THRESHOLD (0.25) real for the `%` operator:
            # its gate reads the `pg_trgm.similarity_threshold` GUC (default 0.3,
            # which shadows 0.25). SET LOCAL scopes it to this transaction only.
            db.execute(
                text("SET LOCAL pg_trgm.similarity_threshold = :t"),
                {"t": TRGM_THRESHOLD},
            )
            # is_variant = candidate (dash/ws-normalized) is a prefix-extension of
            # the input (normalized). Booleans sort true-first under DESC, so
            # curated-looking variants (SRTKT71SS, -BL/-GM) beat digit-neighbours
            # (SRTKT72SS) even when raw similarity ties (§3.2). Self is excluded.
            scope_sql, scope_params = _company_scope_sql(db)
            n_code = _norm_sql("product_code")
            # SAVEPOINT: this lookup is best-effort, and a failed statement
            # otherwise leaves the whole transaction aborted - every later
            # query in the request would die on InFailedSqlTransaction.
            with db.begin_nested():
                rows = db.execute(
                    text(
                        f"""
                        SELECT id, product_code, product_name,
                               similarity({n_code}, :pn) AS sim,
                               (left({n_code}, length(:pn)) = :pn) AS is_variant
                        FROM products
                        WHERE {n_code} % :pn
                          AND {n_code} <> :pn{scope_sql}
                        ORDER BY is_variant DESC, sim DESC, product_code
                        LIMIT :n
                        """
                    ),
                    {"pn": norm, "n": TRGM_LIMIT, **scope_params},
                ).all()
            # A did-you-mean must not offer a placeholder the exact tier just
            # refused to answer with.
            visible = _chat_visible_product_ids(db, [r.id for r in rows])
            for r in rows:
                if str(r.id) not in visible:
                    continue
                sim = float(r.sim or 0.0)
                if sim < TRGM_THRESHOLD:
                    continue
                is_variant = bool(r.is_variant)
                out.append(
                    ResolvedEntity(
                        entity_type="product",
                        canonical_code=r.product_code,
                        uuid=str(r.id) if r.id else None,
                        match_field="product_code",
                        match_tier="trgm",
                        similarity=sim,
                        display={"product_name": r.product_name, "is_variant": is_variant},
                    )
                )
        except Exception:
            logger.exception("trgm product probe failed for phrase=%s", phrase)

    if "customer" in allowed_entity_types:
        try:
            # The emitted uuid is the JOINED customer row, so both sides are
            # scoped: the order we matched on AND the customer we hand back.
            order_scope_sql, scope_params = _company_scope_sql(db, "o.company_id")
            # An empty scope needs no customer clause: the order clause is already
            # ` AND FALSE`, which returns nothing to join against.
            cust_scope_sql = (
                " AND (c.id IS NULL OR c.company_id::text = ANY(:__company_ids))"
                if scope_params
                else ""
            )
            # No COALESCE: GREATEST ignores NULL args and a NULL `%` operand is
            # simply not a match, so the bare column keeps the expression
            # byte-identical to the functional index (migration 410).
            n_dname = _norm_sql("o.debtor_name")
            n_dcode = _norm_sql("o.debtor_code")
            # SAVEPOINT: this lookup is best-effort, and a failed statement
            # otherwise leaves the whole transaction aborted - every later
            # query in the request would die on InFailedSqlTransaction.
            with db.begin_nested():
                rows = db.execute(
                    text(
                        f"""
                        SELECT o.debtor_name, o.debtor_code,
                               c.id AS customer_id,
                               GREATEST(
                                   similarity({n_dname}, :pn),
                                   similarity({n_dcode}, :pn)
                               ) AS sim
                        FROM orders o
                        LEFT JOIN customers c ON lower(btrim(c.customer_name)) = lower(btrim(o.debtor_name))
                        WHERE o.deleted_at IS NULL
                          AND o.debtor_name IS NOT NULL
                          AND ({n_dname} % :pn OR {n_dcode} % :pn){order_scope_sql}{cust_scope_sql}
                        GROUP BY o.debtor_name, o.debtor_code, c.id
                        ORDER BY sim DESC
                        LIMIT :n
                        """
                    ),
                    {"pn": norm, "n": TRGM_LIMIT, **scope_params},
                ).all()
            for r in rows:
                sim = float(r.sim or 0.0)
                if sim < TRGM_THRESHOLD:
                    continue
                out.append(
                    ResolvedEntity(
                        entity_type="customer",
                        canonical_code=r.debtor_name,
                        uuid=str(r.customer_id) if r.customer_id else None,
                        match_field="debtor_name",
                        match_tier="trgm",
                        similarity=sim,
                        display={"debtor_name": r.debtor_name, "debtor_code": r.debtor_code, "source": "orders"},
                    )
                )
            scope_sql, scope_params = _company_scope_sql(db)
            n_ccode = _norm_sql("customer_code")
            n_cname = _norm_sql("customer_name")
            # SAVEPOINT: this lookup is best-effort, and a failed statement
            # otherwise leaves the whole transaction aborted - every later
            # query in the request would die on InFailedSqlTransaction.
            with db.begin_nested():
                rows = db.execute(
                    text(
                        f"""
                        SELECT id, customer_code, customer_name,
                               GREATEST(
                                   similarity({n_ccode}, :pn),
                                   similarity({n_cname}, :pn)
                               ) AS sim
                        FROM customers
                        WHERE ({n_ccode} % :pn OR {n_cname} % :pn){scope_sql}
                        ORDER BY sim DESC
                        LIMIT :n
                        """
                    ),
                    {"pn": norm, "n": TRGM_LIMIT, **scope_params},
                ).all()
            for r in rows:
                sim = float(r.sim or 0.0)
                if sim < TRGM_THRESHOLD:
                    continue
                out.append(
                    ResolvedEntity(
                        entity_type="customer",
                        canonical_code=r.customer_code,
                        uuid=str(r.id) if r.id else None,
                        match_field="customer_code",
                        match_tier="trgm",
                        similarity=sim,
                        display={"customer_name": r.customer_name},
                    )
                )
        except Exception:
            logger.exception("trgm customer probe failed for phrase=%s", phrase)

    if "customer_order" in allowed_entity_types:
        try:
            scope_sql, scope_params = _company_scope_sql(db)
            n_onum = _norm_sql("order_number")
            # SAVEPOINT: this lookup is best-effort, and a failed statement
            # otherwise leaves the whole transaction aborted - every later
            # query in the request would die on InFailedSqlTransaction.
            with db.begin_nested():
                rows = db.execute(
                    text(
                        f"""
                        SELECT id, order_number,
                               similarity({n_onum}, :pn) AS sim
                        FROM orders
                        WHERE deleted_at IS NULL AND {n_onum} % :pn{scope_sql}
                        ORDER BY sim DESC
                        LIMIT :n
                        """
                    ),
                    {"pn": norm, "n": TRGM_LIMIT, **scope_params},
                ).all()
            for r in rows:
                sim = float(r.sim or 0.0)
                if sim < TRGM_THRESHOLD:
                    continue
                out.append(
                    ResolvedEntity(
                        entity_type="customer_order",
                        canonical_code=r.order_number,
                        uuid=str(r.id) if r.id else None,
                        match_field="order_number",
                        match_tier="trgm",
                        similarity=sim,
                        display={},
                    )
                )
        except Exception:
            logger.exception("trgm order probe failed for phrase=%s", phrase)

    if "promotion" in allowed_entity_types:
        try:
            scope_sql, scope_params = _company_scope_sql(db)
            n_desc = _norm_sql("description")
            # SAVEPOINT: this lookup is best-effort, and a failed statement
            # otherwise leaves the whole transaction aborted - every later
            # query in the request would die on InFailedSqlTransaction.
            with db.begin_nested():
                rows = db.execute(
                    text(
                        f"""
                        SELECT id, description,
                               similarity({n_desc}, :pn) AS sim
                        FROM promotions
                        WHERE {n_desc} % :pn{scope_sql}
                        ORDER BY sim DESC
                        LIMIT :n
                        """
                    ),
                    {"pn": norm, "n": TRGM_LIMIT, **scope_params},
                ).all()
            for r in rows:
                sim = float(r.sim or 0.0)
                if sim < TRGM_THRESHOLD:
                    continue
                out.append(
                    ResolvedEntity(
                        entity_type="promotion",
                        canonical_code=str(r.id),
                        uuid=str(r.id),
                        match_field="promotion_id",
                        match_tier="trgm",
                        similarity=sim,
                        display={"description": r.description},
                    )
                )
        except Exception:
            logger.exception("trgm promotion probe failed for phrase=%s", phrase)

    if "transporter" in allowed_entity_types:
        try:
            scope_sql, scope_params = _company_scope_sql(db)
            n_tcode = _norm_sql("code")
            n_tname = _norm_sql("name")
            n_tnorm = _norm_sql("normalized_name")
            # SAVEPOINT: this lookup is best-effort, and a failed statement
            # otherwise leaves the whole transaction aborted - every later
            # query in the request would die on InFailedSqlTransaction.
            with db.begin_nested():
                rows = db.execute(
                    text(
                        f"""
                        SELECT id, code, name, normalized_name,
                               GREATEST(
                                   similarity({n_tcode}, :pn),
                                   similarity({n_tname}, :pn),
                                   similarity({n_tnorm}, :pn)
                               ) AS sim
                        FROM transporters
                        WHERE ({n_tcode} % :pn OR {n_tname} % :pn
                               OR {n_tnorm} % :pn){scope_sql}
                        ORDER BY sim DESC
                        LIMIT :n
                        """
                    ),
                    {"pn": norm, "n": TRGM_LIMIT, **scope_params},
                ).all()
            for r in rows:
                sim = float(r.sim or 0.0)
                if sim < TRGM_THRESHOLD:
                    continue
                out.append(
                    ResolvedEntity(
                        entity_type="transporter",
                        canonical_code=r.code,
                        uuid=str(r.id) if r.id else None,
                        match_field="transporter_code",
                        match_tier="trgm",
                        similarity=sim,
                        display={"code": r.code, "name": r.name},
                    )
                )
        except Exception:
            logger.exception("trgm transporter probe failed for phrase=%s", phrase)

    # Primary key is the product `is_variant` prefix-boost (§3.2): a candidate
    # whose normalized code is a prefix-extension of the input beats a mere
    # digit-neighbour even when raw similarity ties (SRTKT71SS-FRG > SRTKT72SS).
    # Non-product hits never carry the flag, so this only reorders products.
    # Secondary key keeps the existing similarity-desc ordering.
    out.sort(key=lambda e: (bool(e.display.get("is_variant")), e.similarity or 0.0), reverse=True)
    return out[: TRGM_LIMIT * 2]


def find_entity_neighbours_with_data(
    db: Session,
    product_code: str,
    *,
    has_data: Callable[[list[str]], set[str]],
    limit: int = 3,
    suggest_floor: float = SUGGEST_FLOOR,
) -> list[dict[str, Any]]:
    """Return data-bearing product neighbours for a **data-miss** (§3.3 / §7.2 AC-N2/N3/N4).

    The input product resolved to a real row but the asked domain (stock / eta / ...)
    returned zero rows. This finds real substitute products the user could ask about
    instead, but ONLY ones that actually have data in that domain.

    Candidate pool (deduped by product id):
      (a) stored variant graph - children (``variant_of_id == input.id``),
          siblings sharing the input's parent (``variant_of_id == input.variant_of_id``),
          and the input's own parent / base product (``id == input.variant_of_id``).
          These are curated variants; they carry ``is_variant=True``.
      (b) trigram neighbours via :func:`_trgm_lookup` (recall), where ``is_variant``
          is the prefix-extension boost from §3.2.

    Ranking: ``is_variant DESC, sim DESC`` (curated variants beat digit-neighbours).

    Gates, applied AFTER ranking and BEFORE the cap so out-of-domain / too-far
    candidates never consume a top-N slot:
      * **Has-data gate** (AC-N3) - ``has_data(candidate_ids) -> {ids_with_data}`` is a
        caller-supplied BATCH predicate (one query, per-domain: only the stock tool
        knows stock, only incoming knows eta). A candidate absent from the returned set
        is dropped, even if highly similar. So `8517` no-stock AND `8518` no-stock ⇒
        `8518` is not returned; the walk continues to the next neighbour WITH stock.
      * **Distance floor** (AC-N4) - drop candidates with ``sim < suggest_floor``.
        Graph variants are scored with ``similarity()`` too, so the floor applies
        UNIFORMLY (a curated variant that is textually far AND is the only data-bearing
        option still yields ``[]`` - "no similar products with stock").

    Returns up to ``limit`` dicts ``{value, display, id, sim, is_variant}`` (``value`` =
    the product_code the user should ask about next). Empty list ⇒ caller emits no
    alternatives.
    """
    code = (product_code or "").strip()
    if not code:
        return []
    try:
        return _find_entity_neighbours_with_data(
            db, code, has_data=has_data, limit=limit, suggest_floor=suggest_floor
        )
    except Exception:
        # Best-effort enrichment - a suggestion probe must NEVER turn the caller's
        # (already-committed) empty result into a 500. Includes DBs that can't run
        # regexp_replace / similarity (e.g. sqlite test fixtures).
        logger.exception("neighbour suggestion probe failed for code=%s", code)
        return []


def _find_entity_neighbours_with_data(
    db: Session,
    code: str,
    *,
    has_data: Callable[[list[str]], set[str]],
    limit: int,
    suggest_floor: float,
) -> list[dict[str, Any]]:
    norm = _strip_all_ws(code.lower())
    input_row = (
        db.query(Product.id, Product.product_code, Product.variant_of_id)
        .filter(_ws_insensitive_lower(Product.product_code) == norm)
        .first()
    )
    input_id = str(input_row.id) if input_row and input_row.id else None
    parent_id = (
        str(input_row.variant_of_id)
        if input_row and input_row.variant_of_id
        else None
    )

    # id -> candidate dict. Graph members inserted first so they win on dedupe.
    candidates: dict[str, dict[str, Any]] = {}

    # (a) Stored variant graph: children of the input, siblings sharing its parent,
    #     and the input's own parent (the base product) when it is itself a variant.
    if input_id:
        try:
            scope_sql, scope_params = _company_scope_sql(db)
            # Scored the same way as `_trgm_lookup` (stripped both sides): a curated
            # sibling stored as "SRT-FH12-CR-DIY-BL" must not be cut by
            # SUGGEST_FLOOR just because the caller sent "srtfh12crdiy".
            n_code = _norm_sql("product_code")
            graph_rows = db.execute(
                text(
                    f"""
                    SELECT id, product_code, product_name,
                           similarity({n_code}, :pn) AS sim
                    FROM products
                    WHERE id <> :pid
                      AND (variant_of_id = :pid
                           OR (:parent_id IS NOT NULL AND variant_of_id = :parent_id)
                           OR (:parent_id IS NOT NULL AND id = :parent_id)){scope_sql}
                    """
                ),
                {"pn": norm, "pid": input_id, "parent_id": parent_id, **scope_params},
            ).all()
            visible = _chat_visible_product_ids(db, [r.id for r in graph_rows])
            for r in graph_rows:
                cid = str(r.id)
                if cid not in visible:
                    continue
                candidates[cid] = {
                    "value": r.product_code,
                    "display": r.product_name or r.product_code,
                    "id": cid,
                    "sim": float(r.sim or 0.0),
                    "is_variant": True,
                }
        except Exception:
            logger.exception("variant graph neighbour probe failed for code=%s", code)

    # (b) Trigram neighbours (recall; is_variant = §3.2 prefix-extension boost).
    for ent in _trgm_lookup(db, code, frozenset({"product"})):
        if ent.entity_type != "product" or not ent.uuid:
            continue
        cid = str(ent.uuid)
        if cid == input_id:
            continue
        sim = float(ent.similarity or 0.0)
        is_variant = bool(ent.display.get("is_variant"))
        existing = candidates.get(cid)
        if existing:
            # Already a graph member - keep the curated is_variant=True, take max sim.
            existing["sim"] = max(existing["sim"], sim)
            existing["is_variant"] = existing["is_variant"] or is_variant
        else:
            candidates[cid] = {
                "value": ent.canonical_code,
                "display": ent.display.get("product_name") or ent.canonical_code,
                "id": cid,
                "sim": sim,
                "is_variant": is_variant,
            }

    if not candidates:
        return []

    # Rank: is_variant DESC, sim DESC.
    ranked = sorted(
        candidates.values(),
        key=lambda c: (c["is_variant"], c["sim"]),
        reverse=True,
    )

    # HAS-DATA GATE (batch - one query supplied by the caller).
    try:
        with_data = has_data([c["id"] for c in ranked]) or set()
    except Exception:
        logger.exception("has_data predicate failed for code=%s", code)
        return []
    ranked = [c for c in ranked if c["id"] in with_data]

    # DISTANCE FLOOR (uniform - applies to graph variants too).
    ranked = [c for c in ranked if c["sim"] >= suggest_floor]

    return [
        {
            "value": c["value"],
            "display": c["display"],
            "id": c["id"],
            "sim": round(c["sim"], 3),
            "is_variant": c["is_variant"],
        }
        for c in ranked[:limit]
    ]


# --------------------------------------------------------------------------- #
# AND-mode (cross-token intersection) probes
# --------------------------------------------------------------------------- #
# When the caller asks for "cabana filter tap", a per-token OR resolver yields
# two ambiguous lists that the agent must intersect manually - and the PREFIX
# limit can evict legitimate cross-matches before that intersection runs.
#
# AND-mode runs a single SQL per entity type with all tokens ANDed across the
# entity's concatenated searchable columns. Result: only rows whose combined
# text contains EVERY token. Skips code-only entity types (SPO, GRN, inbound
# shipment) where multi-token AND has no semantic meaning.

# Cap per AND probe BEFORE downstream post-filters (access_levels, promotion-
# domain product expansion). 200 is the same ceiling the API's `limit` param
# uses - keeps the SQL bounded while ensuring the post-filters see enough rows
# to find legitimate matches. Without this slack, e.g. 22 Sorento+End-User
# promotions get clipped to ~3 because the first 20 raw Sorento rows happen to
# have dealer-only access_levels.
AND_MODE_LIMIT = 200


def _concat_ws(*cols):
    """Sqlalchemy concat_ws helper used by AND-mode probes.

    Note the blob is space-joined, so the normalized half of `_norm_contains`
    also drops the join separator - a word can in principle match across two
    concatenated columns. `_NORM_MIN_LEN` bounds that; the plain ILIKE half is
    unaffected."""
    return func.concat_ws(" ", *cols)


def _word_variants(word: str) -> list[str]:
    """Return `word` plus a singular fallback when it looks like an alphabetic
    plural. Lets a token like 'ventures' still match 'VENTURE ENTERPRISE' so
    minor plural typos ('Fira ventures' vs DB 'FIRA VENTURE') don't drop the
    match. Codes containing digits are NEVER stripped - `TT440s` must keep its
    trailing s.

    Hand pass 11, defect 4: 'catalog'/'catalogue' is the same US/UK spelling
    swap `references.py::_resolve_attachment_type_for_hint` already treats as
    one word - a customer typing "cabana catalogue" must still cover a file
    literally named "... CATALOG ..." (measured against the clone: the Cabana
    file has no "UE", the Sorento one does). Same precedent, same word pair,
    applied here so the AND-mode filename-coverage probe sees it too.
    """
    if not word:
        return []
    out = [word]
    low = word.lower()
    if (
        len(word) >= 4
        and low.endswith("s")
        and not low.endswith("ss")
        and word.isalpha()
    ):
        out.append(word[:-1])
    if low == "catalog":
        out.append("catalogue")
    elif low == "catalogue":
        out.append("catalog")
    return out


def _and_token_match_counts(blob, tokens: list[str]):
    """Return one match-count expression per token over `blob`.

    Each expression is `sum(0/1 indicators)` across the token's words, where
    each word's indicator is `blob ILIKE %word%` OR a singular-fallback
    variant. The probe uses these expressions to find the GLOBAL MAX match
    count for each token across the table, then keeps only rows hitting that
    max - so filler words ("promotion", "version") never poison results: any
    row that hits the most semantic words wins, regardless of how many words
    the token had.
    """
    counts = []
    for tok in tokens or []:
        if not tok:
            continue
        words = [w for w in tok.split() if w]
        if not words:
            continue
        indicators = []
        for word in words:
            variants = _word_variants(word)
            if not variants:
                continue
            ilike_any = or_(*[_norm_contains(blob, v) for v in variants])
            indicators.append(case((ilike_any, 1), else_=0))
        if not indicators:
            continue
        counts.append(reduce(add, indicators))
    return counts


def _and_max_tier_filter(base_query, counts):
    """Given a base query already filtered by non-token predicates and a list
    of per-token match-count expressions, return the additional filter that
    keeps only rows reaching the GLOBAL MAX match count for EVERY token.

    Returns `None` when any token has no possible hits (max == 0) - caller
    should short-circuit and return no rows.
    """
    if not counts:
        return None
    maxes = base_query.with_entities(*[func.max(c) for c in counts]).one()
    if any((m is None or m == 0) for m in maxes):
        return None
    return and_(*[c == m for c, m in zip(counts, maxes)])


def _and_probe_product(db: Session, tokens: list[str]) -> list[ResolvedEntity]:
    # Product resolution is CODE-ONLY by design (see _prefix_probe_product): a token
    # must hit product_code, never product_name/description. A description cross-ref
    # like "USED FOR SRTWC6015-RL-UF" must NOT resolve that row for token SRTWC6015.
    blob = Product.product_code
    counts = _and_token_match_counts(blob, tokens)
    # Filtered BEFORE the per-token max is taken, so a hidden row can neither be
    # returned nor set the bar the visible rows have to reach.
    base = db.query(
        Product.id, Product.product_code, Product.product_name, Product.is_active
    ).filter(chat_searchable_products())
    tier = _and_max_tier_filter(base, counts)
    if tier is None:
        return []
    rows = base.filter(tier).order_by(*_product_family_order()).limit(AND_MODE_LIMIT).all()
    return [
        ResolvedEntity(
            entity_type="product",
            canonical_code=code,
            uuid=str(pid) if pid else None,
            match_field="product_code",
            match_tier="and",
            match_blob=code,
            display={"product_name": name, "is_active": bool(is_active)},
        )
        for pid, code, name, is_active in rows
    ]


def _and_probe_promotion(db: Session, tokens: list[str]) -> list[ResolvedEntity]:
    counts = _and_token_match_counts(Promotion.description, tokens)
    base = db.query(Promotion.id, Promotion.description, Promotion.is_active)
    tier = _and_max_tier_filter(base, counts)
    if tier is None:
        return []
    rows = base.filter(tier).limit(AND_MODE_LIMIT).all()
    return [
        ResolvedEntity(
            entity_type="promotion",
            canonical_code=str(pid),
            uuid=str(pid),
            match_field="description",
            match_tier="and",
            match_blob=description,
            display={"description": description, "is_active": bool(is_active)},
        )
        for pid, description, is_active in rows
    ]


def _token_coverage_expr(blob, tokens: list[str]):
    """SQL expr counting how many distinct query tokens `blob` contains.

    A token is "present" iff ALL of its words appear in the blob (multi-word
    tokens like "water closet" need both words). Returns None when no token
    yields a usable indicator. Unlike `_and_token_match_counts` (per-token
    global-max), this scores COVERAGE per row - so a descriptive token that
    happens to match unrelated rows elsewhere in the corpus cannot exclude an
    otherwise-best match.
    """
    indicators = []
    for tok in tokens or []:
        if not tok:
            continue
        words = [w for w in tok.split() if w]
        if not words:
            continue
        word_preds = []
        for word in words:
            variants = _word_variants(word)
            if not variants:
                continue
            word_preds.append(or_(*[_norm_contains(blob, v) for v in variants]))
        if not word_preds:
            continue
        indicators.append(case((and_(*word_preds), 1), else_=0))
    if not indicators:
        return None
    return reduce(add, indicators)


def _and_probe_attachment(
    db: Session, tokens: list[str], *, coverage_mode: bool = False
) -> list[ResolvedEntity]:
    """Attachment AND probe.

    Default (``coverage_mode=False``): the standard cross-token behaviour shared
    by every entity type - concat blob of filename + description + type, kept to
    rows hitting each token's per-token global max.

    ``coverage_mode=True`` (enabled ONLY when the caller's domain is
    ``resource_attachment`` - see ``resolve_references_intersection``): a targeted
    filename-coverage AND. Returns the rows whose ``original_filename`` covers the
    MOST query tokens, ignoring a descriptive token that no filename contains
    instead of letting it poison the AND to empty. "Sorento water closet warranty"
    -> "Warranty Policy_Sorento.pdf" / "warranty_poster SORENTO.pdf" (cover
    Sorento+warranty = 2); Cabana/Mocha warranty files (1 token) are dropped.
    """
    base = (
        db.query(
            Attachment.id,
            Attachment.original_filename,
            Attachment.description,
            Attachment.mime_type,
            Attachment.full_directory_path,
            AttachmentType.type_name,
        )
        .outerjoin(AttachmentType, AttachmentType.id == Attachment.attachment_type_id)
        .filter(Attachment.is_deleted.is_(False))
    )

    if coverage_mode:
        n_tokens = len([t for t in (tokens or []) if t and t.strip()])
        coverage = _token_coverage_expr(Attachment.original_filename, tokens)
        if coverage is None:
            return []
        max_cov = base.with_entities(func.max(coverage)).scalar()
        # Require a genuine conjunction: with 2+ tokens the best row must cover at
        # least 2. If nothing covers 2+ (max_cov==1), AND fails -> return [] so the
        # caller's per-token OR fallback handles it, rather than dumping every
        # single-token match (which would make AND behave like OR).
        if not max_cov or max_cov < min(2, n_tokens):
            return []
        tier = coverage == max_cov
    else:
        blob = _concat_ws(Attachment.original_filename, Attachment.description, AttachmentType.type_name)
        counts = _and_token_match_counts(blob, tokens)
        tier = _and_max_tier_filter(base, counts)
        if tier is None:
            return []

    rows = base.filter(tier).limit(AND_MODE_LIMIT).all()
    return [
        ResolvedEntity(
            entity_type="attachment",
            canonical_code=filename,
            uuid=str(aid) if aid else None,
            match_field="original_filename",
            match_tier="and",
            # Coverage mode scores the filename ALONE; the default mode scores the
            # concat blob. Reporting the wrong one would credit a word to text the
            # probe never looked at.
            match_blob=(
                filename
                if coverage_mode
                else " ".join(x for x in (filename, description, type_name) if x)
            ),
            display={
                "filename": filename,
                "description": description,
                "attachment_type": type_name,
                "mime_type": mime,
                "directory": dir_path,
            },
        )
        for aid, filename, description, mime, dir_path, type_name in rows
    ]


class _ProbeRows(list):
    """A probe's row list plus a truncation marker the probe sets itself.

    The generic loop check (`len(rows) >= AND_MODE_LIMIT`) is blind to a probe
    that merges MULTIPLE capped queries and dedups: the merged list can land
    under the cap while one source query hit its LIMIT, silently under-
    reporting truncation. A probe that knows better says so here.
    """

    truncated: bool = False


def _and_probe_customer(db: Session, tokens: list[str]) -> list[ResolvedEntity]:
    # Two sources: customers master (preferred - has UUID) and orders.debtor_name
    # (legacy fallback). Run both; orders.debtor_name JOINs Customer for UUID.
    out: _ProbeRows = _ProbeRows()
    blob_c = _concat_ws(Customer.customer_code, Customer.customer_name, Customer.phone_number, Customer.email)
    counts_c = _and_token_match_counts(blob_c, tokens)
    base_c = db.query(Customer.id, Customer.customer_code, Customer.customer_name, Customer.phone_number, Customer.email, Customer.is_active)
    tier_c = _and_max_tier_filter(base_c, counts_c)
    if tier_c is not None:
        rows = base_c.filter(tier_c).limit(AND_MODE_LIMIT).all()
        if len(rows) >= AND_MODE_LIMIT:
            out.truncated = True
        for cid, code, name, phone, email, is_active in rows:
            out.append(
                ResolvedEntity(
                    entity_type="customer",
                    canonical_code=code,
                    uuid=str(cid) if cid else None,
                    match_field="customer_code",
                    match_tier="and",
                    match_blob=" ".join(x for x in (code, name, phone, email) if x),
                    display={"customer_name": name, "phone_number": phone, "email": email, "is_active": bool(is_active) if is_active is not None else True},
                )
            )
    # Legacy debtor_name path - only if no customers master hit covers the same name.
    seen_names = {(m.display or {}).get("customer_name", "").lower() for m in out}
    blob_o = _concat_ws(Order.debtor_name, Order.debtor_code)
    counts_o = _and_token_match_counts(blob_o, tokens)
    base_o = (
        db.query(Order.debtor_name, Order.debtor_code, Customer.id)
        .outerjoin(Customer, func.lower(func.btrim(Customer.customer_name)) == func.lower(func.btrim(Order.debtor_name)))
        .filter(Order.deleted_at.is_(None), Order.debtor_name.isnot(None))
    )
    tier_o = _and_max_tier_filter(base_o, counts_o)
    if tier_o is not None:
        rows = base_o.filter(tier_o).distinct().limit(AND_MODE_LIMIT).all()
        if len(rows) >= AND_MODE_LIMIT:
            # The dedup below can shrink the merged list under the cap even
            # though this source hit its LIMIT - mark it at the source.
            out.truncated = True
        for debtor_name, debtor_code, customer_id in rows:
            if (debtor_name or "").lower() in seen_names:
                continue
            out.append(
                ResolvedEntity(
                    entity_type="customer",
                    canonical_code=debtor_name,
                    uuid=str(customer_id) if customer_id else None,
                    match_field="debtor_name",
                    match_tier="and",
                    match_blob=" ".join(x for x in (debtor_name, debtor_code) if x),
                    display={"debtor_name": debtor_name, "debtor_code": debtor_code, "source": "orders"},
                )
            )
    return out


def _and_probe_form(db: Session, tokens: list[str]) -> list[ResolvedEntity]:
    blob = _concat_ws(Form.code, Form.name, Form.purpose)
    counts = _and_token_match_counts(blob, tokens)
    base = db.query(Form.id, Form.code, Form.name, Form.purpose, Form.is_active, Form.form_type)
    tier = _and_max_tier_filter(base, counts)
    if tier is None:
        return []
    rows = base.filter(tier).limit(AND_MODE_LIMIT).all()
    return [
        ResolvedEntity(
            entity_type="form",
            canonical_code=code,
            uuid=str(fid) if fid else None,
            match_field="form_name",
            match_tier="and",
            match_blob=" ".join(x for x in (code, name, purpose) if x),
            display={"form_code": code, "form_name": name, "form_type": form_type, "is_active": bool(is_active)},
        )
        for fid, code, name, purpose, is_active, form_type in rows
    ]


def _and_probe_transporter(db: Session, tokens: list[str]) -> list[ResolvedEntity]:
    blob = _concat_ws(Transporter.code, Transporter.name, Transporter.normalized_name)
    counts = _and_token_match_counts(blob, tokens)
    # `normalized_name` is selected only so `match_blob` can mirror the scored
    # blob exactly; it is not displayed. Widening the SELECT does not change
    # which rows come back.
    base = db.query(Transporter.id, Transporter.code, Transporter.name, Transporter.normalized_name)
    tier = _and_max_tier_filter(base, counts)
    if tier is None:
        return []
    rows = base.filter(tier).limit(AND_MODE_LIMIT).all()
    return [
        ResolvedEntity(
            entity_type="transporter",
            canonical_code=code,
            uuid=str(tid) if tid else None,
            match_field="transporter_name",
            match_tier="and",
            match_blob=" ".join(x for x in (code, name, normalized_name) if x),
            display={"code": code, "name": name},
        )
        for tid, code, name, normalized_name in rows
    ]


def _and_probe_warehouse(db: Session, tokens: list[str]) -> list[ResolvedEntity]:
    """Same ban as `_prefix_probe_warehouse`: no AND-mode fan-out for warehouse, exact
    normalized code per token only, deduped across tokens.

    ALL OR NOTHING across the tokens, like every other AND probe: this probe answers only
    when EVERY token is itself a warehouse code. Exact-code-per-token alone broke the AND
    contract ("rows matching EVERY token") in the one way that matters to the chatbot -
    "SRT62-GM to brw" answered with BRW, which does not match "SRT62-GM", and that
    non-empty intersection suppressed `_resolve_input`'s own "AND-mode produced zero
    intersection; switched to OR-mode under the whitelist" degrade. That degrade is what
    resolves a product and a warehouse SEPARATELY, so the product was lost and the
    chatbot's fetch args carried `warehouse_ids` with no `product_ids` (measured, browser
    AC-10, 8 Sep 2026; 161 of the 166 multi-hint AND turns in the corpus already ride the
    degrade and were never affected).
    """
    seen: set[str] = set()
    out: list[ResolvedEntity] = []
    hits = _probe_warehouse(db, tokens)
    if any(not hits.get(tok) for tok in tokens):
        return []
    for tok in tokens:
        for m in hits.get(tok, []):
            if m.uuid:
                if m.uuid in seen:
                    continue
                seen.add(m.uuid)
            out.append(m)
    return out


def _and_probe_supplier(db: Session, tokens: list[str]) -> list[ResolvedEntity]:
    blob = _concat_ws(Supplier.supplier_code, Supplier.supplier_name, Supplier.contact_name)
    counts = _and_token_match_counts(blob, tokens)
    # `contact_name` is selected only so `match_blob` mirrors the scored blob.
    base = db.query(Supplier.id, Supplier.supplier_code, Supplier.supplier_name, Supplier.is_active, Supplier.contact_name)
    tier = _and_max_tier_filter(base, counts)
    if tier is None:
        return []
    rows = base.filter(tier).limit(AND_MODE_LIMIT).all()
    return [
        ResolvedEntity(
            entity_type="supplier",
            canonical_code=code,
            uuid=str(sid) if sid else None,
            match_field="supplier_code",
            match_tier="and",
            match_blob=" ".join(x for x in (code, name, contact_name) if x),
            display={"supplier_name": name, "is_active": bool(is_active) if is_active is not None else True},
        )
        for sid, code, name, is_active, contact_name in rows
    ]


def _and_probe_customer_order(db: Session, tokens: list[str]) -> list[ResolvedEntity]:
    # Code-only entity. Match exclusively on `order_number` so generic abbreviations
    # like "DO" (delivery order) don't accidentally hit any debtor_name containing
    # the substring (e.g. customer "MOCHA SDN BHD (DOCUMENT)" matched "DO"). Anyone
    # filtering orders by customer name should resolve the customer separately and
    # pass the customer entity, not bundle the name into a customer_order token.
    conds = [_norm_contains(Order.order_number, t) for t in tokens if t]
    if not conds:
        return []
    rows = (
        db.query(Order.id, Order.order_number, Order.debtor_name, Order.actual_delivery_date)
        .filter(Order.deleted_at.is_(None), *conds)
        .limit(AND_MODE_LIMIT)
        .all()
    )
    return [
        ResolvedEntity(
            entity_type="customer_order",
            canonical_code=row.order_number,
            uuid=str(row.id) if row.id else None,
            match_field="order_number",
            match_tier="and",
            match_blob=row.order_number,
            display={
                "customer_name": row.debtor_name,
                "actual_delivery_date": _iso(row.actual_delivery_date),
            },
        )
        for row in rows
    ]


_AND_PROBES: tuple[tuple[Callable[[Session, list[str]], list[ResolvedEntity]], frozenset[str]], ...] = (
    (_and_probe_product, frozenset({"product"})),
    (_and_probe_promotion, frozenset({"promotion"})),
    (_and_probe_attachment, frozenset({"attachment"})),
    (_and_probe_customer, frozenset({"customer"})),
    (_and_probe_form, frozenset({"form"})),
    (_and_probe_transporter, frozenset({"transporter"})),
    (_and_probe_warehouse, frozenset({"warehouse"})),
    (_and_probe_supplier, frozenset({"supplier"})),
    (_and_probe_customer_order, frozenset({"customer_order"})),
)


def token_word_coverage_for_rows(
    tokens: list[str],
    rows: list[dict[str, Any]],
    truncated_types: frozenset[str] | set[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Per token, per entity type: which of the token's words appear in the
    rows being returned.

    AND-mode is max-coverage, not a boolean AND (see `_and_max_tier_filter`),
    so a word can contribute nothing and the caller cannot tell from the rows.
    This reports it, without changing which rows come back.

    Called by the API layer over the FINAL row set - after the access-level
    filter, the promotion expansion and the limit - because coverage computed
    earlier describes rows a later stage removed (the original version baked
    it into ``as_dict()`` and asserted "every word matched" on zero-row
    entitlement-filtered responses).

    Rules that keep the field honest:
      * The claim is WEAK: "absent from these results", never "absent from the
        catalogue". No extra query.
      * Partitioned per entity type. A word matched by a product code must not
        be credited to a promotion enquiry - pooling across types was the
        other way the first version lied.
      * Scored against the row's ``_match_blob`` (the text its probe actually
        scored), never ``display``: the product probe matches ``product_code``
        only, so a word appearing in ``product_name`` would be a match the
        probe never made. Promotion rows whose ``match_field`` is
        ``description`` fall back to ``display.description`` - for those rows
        blob and description are the same column, which keeps the rows the
        promotion expander synthesized from a description match honest. The
        gate matters: a promotion found by MEMBERSHIP
        (``match_field="promotion_membership"``, the "check the promo for
        <SKU>" walk) was never scored on its description, and scoring it
        anyway invented ``unmatched=[<SKU>]`` on exactly the rows that ARE
        that SKU's promotions.
      * A type whose rows carry no scorable text is OMITTED, not reported
        all-unmatched: nobody scored any text for those rows. A type where
        SOME rows are unscored keeps its claims but is marked ``truncated`` - 
        the claims cover only the scored subset.
      * A word matched via a `_word_variants` form counts as matched but is
        echoed back AS THE CALLER TYPED IT - reporting "taps" unmatched while
        showing a TAP promotion would be a new false statement.
      * ``truncated_types`` marks a type whose row set was capped (probe limit
        or API ``limit``), so a consumer knows an unmatched word may exist in
        rows that were cut and must not phrase it as a catalogue-wide absence.
    """
    blobs_by_type: dict[str, list[str]] = {}
    # Types with at least one surviving row nobody scored (no blob, no honest
    # fallback). Their claims cover only the scored subset, so they read as
    # incomplete - reported via the same `truncated` flag.
    partially_scored: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        etype = str(row.get("entity_type") or "")
        if not etype:
            continue
        blob = row.get("_match_blob")
        if (
            not blob
            and etype == "promotion"
            and row.get("match_field") == "description"
        ):
            # Only description-MATCHED promotion rows may borrow the display
            # description as their blob; membership-derived rows
            # (match_field="promotion_membership") were never scored on it.
            blob = (row.get("display") or {}).get("description")
        if blob:
            blobs_by_type.setdefault(etype, []).append(str(blob).lower())
        else:
            partially_scored.add(etype)

    out: list[dict[str, Any]] = []
    for tok in tokens or []:
        if not tok or not isinstance(tok, str):
            continue
        coverage: list[dict[str, Any]] = []
        for etype in sorted(blobs_by_type):
            blobs = blobs_by_type[etype]
            norm_blobs = [_strip_all_ws(b) for b in blobs]
            matched: list[str] = []
            unmatched: list[str] = []
            # D13 (owner turn, 8 Sep 2026, "CB6622-PP?"). Two separate fixes:
            #  1. a trailing "?" (or !.,) on the query is punctuation, not a fifth
            #     character of the code beside it - a row's own `_match_blob` never
            #     carries it, so an un-stripped word read as "unmatched" against a row
            #     that answered it exactly.
            #  2. the CALLER's own token is already dash/ws-stripped by the time it
            #     reaches here (`resolve_entity_body`/`_strip_entity_stopwords` send
            #     "cb6622pp"), while `_match_blob` is the RAW column value
            #     ("CB6622-PP") - the same §3a normalization the code PROBE itself
            #     used to decide this row was the answer (`_ws_insensitive_lower`)
            #     has to run here too, or a dash-insensitive match reads as unanswered
            #     by a coverage check that never dropped the dash.
            for word in [w.rstrip("?!.,") for w in tok.split() if w.rstrip("?!.,")]:
                variants = [v.lower() for v in _word_variants(word)]
                norm_variants = [_strip_all_ws(v) for v in variants]
                hit = any(v in blob for blob in blobs for v in variants) or any(
                    nv and nv in nblob for nblob in norm_blobs for nv in norm_variants
                )
                (matched if hit else unmatched).append(word)
            coverage.append(
                {
                    "entity_type": etype,
                    "matched_words": matched,
                    "unmatched_words": unmatched,
                    "truncated": etype in truncated_types or etype in partially_scored,
                }
            )
        out.append(
            {
                "token": tok,
                # What `match_mode: "and"` really means. Nested here rather
                # than top-level: consumer chains spread top-level keys toward
                # persisted session state, and `by_entity_type`'s keys are
                # rendered to customers as entity types.
                "match_semantics": "max_coverage",
                "coverage": coverage,
            }
        )
    return out


@dataclass
class IntersectionResolutionResult:
    """Cross-token AND result. Returned when match_mode=and."""

    tokens: list[str]
    intersection: list[ResolvedEntity]
    elapsed_ms: float
    match_mode: str = "and"
    # Fuzzy "did you mean" neighbours when the intersection is empty (union of the
    # tokens' trigram neighbours, deduped). Same entity-level, non-domain-gated
    # semantics as the OR-mode per-token alternatives.
    alternatives: list[ResolvedEntity] = field(default_factory=list)
    # Entity types whose AND probe returned AND_MODE_LIMIT rows - the cap may
    # have cut real matches, so word-coverage claims over these types are
    # incomplete. (Exactly-at-the-cap without truncation is a harmless false
    # positive: the flag only softens phrasing downstream.)
    truncated_entity_types: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.intersection

    def as_dict(self) -> dict[str, Any]:
        # Group hits by entity_type for stable agent-facing shape.
        by_type: dict[str, list[dict[str, Any]]] = {}
        for m in self.intersection:
            by_type.setdefault(m.entity_type, []).append({
                "entity_type": m.entity_type,
                "canonical_code": m.canonical_code,
                "uuid": m.uuid,
                "match_field": m.match_field,
                "match_tier": m.match_tier,
                "company_id": m.company_id,
                "company_name": m.company_name,
                "company_code": m.company_code,
                "display": m.display,
            })
        result: dict[str, Any] = {
            "match_mode": self.match_mode,
            "tokens": self.tokens,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "intersection": [
                {
                    "entity_type": m.entity_type,
                    "canonical_code": m.canonical_code,
                    "uuid": m.uuid,
                    "match_field": m.match_field,
                    "match_tier": m.match_tier,
                    "company_id": m.company_id,
                    "company_name": m.company_name,
                    "company_code": m.company_code,
                    "display": m.display,
                    # Transport key for `token_word_coverage_for_rows`: the
                    # text this row's probe actually scored. Coverage cannot
                    # be computed here - the API layer filters / replaces /
                    # caps these rows afterwards, and coverage must describe
                    # the FINAL set. Stripped by `_attach_and_coverage`
                    # before the response leaves the process.
                    "_match_blob": m.match_blob,
                }
                for m in self.intersection
            ],
            "by_entity_type": by_type,
            "empty": self.empty,
            "unresolved_tokens": list(self.tokens) if self.empty else [],
            "alternatives": [
                {
                    "entity_type": a.entity_type,
                    "canonical_code": a.canonical_code,
                    "uuid": a.uuid,
                    "match_field": a.match_field,
                    "match_tier": a.match_tier,
                    "similarity": a.similarity,
                    "company_id": a.company_id,
                    "company_name": a.company_name,
                    "company_code": a.company_code,
                    "display": a.display,
                }
                for a in self.alternatives
            ],
        }
        if self.truncated_entity_types:
            # Transport key, same lifecycle as `_match_blob`: tells the API
            # layer which types' probes hit AND_MODE_LIMIT, so an unmatched
            # word there is flagged rather than asserted.
            result["_truncated_entity_types"] = sorted(self.truncated_entity_types)
        return result


def resolve_references_intersection(
    db: Session,
    tokens: list[str],
    *,
    allowed_entity_types: Optional[Iterable[str]] = None,
    domain_hint: Optional[str] = None,
) -> IntersectionResolutionResult:
    """AND-mode resolver. Returns rows matching EVERY token in the concatenated
    searchable columns of each entity type. Skips code-only types.

    `allowed_entity_types` (when set) is always a global set filter - every
    surviving probe sees every token. Positional pairing (token[i] vs
    allowed_entity_types[i]) is no longer auto-triggered on equal-length lists;
    callers that need 1:1 pairing must issue one resolve call per pair.
    """
    raw_tokens = list(tokens or [])
    pair_map = _build_token_type_map(raw_tokens, allowed_entity_types)

    t0 = time.perf_counter()
    clean_tokens = [t.strip() for t in raw_tokens if t and t.strip()]
    if not clean_tokens:
        return IntersectionResolutionResult(tokens=[], intersection=[], elapsed_ms=0.0)

    if pair_map is not None:
        allowed: Optional[frozenset[str]] = frozenset(pair_map.values())
    elif allowed_entity_types is not None:
        # Apply one-to-many expansion (brand / category → product + promotion).
        allowed = _expand_entity_types(allowed_entity_types, domain_hint=domain_hint)
    else:
        allowed = None

    # Filename-coverage AND for attachments is opt-in by domain: only the
    # resource_attachment domain gets the "ignore unmatched token, rank by
    # coverage" behaviour. Every other domain keeps the standard per-token
    # global-max AND so cross-domain attachment lookups are unaffected.
    attachment_coverage = (domain_hint or "").strip().lower() in {"resource_attachment", "attachment"}

    hits: list[ResolvedEntity] = []
    truncated_types: set[str] = set()
    for probe, produces in _AND_PROBES:
        if allowed is not None and produces.isdisjoint(allowed):
            continue
        if pair_map is not None:
            probe_tokens = [t for t in clean_tokens if pair_map.get(t) in produces]
            if not probe_tokens:
                continue
        else:
            probe_tokens = clean_tokens
        try:
            if probe is _and_probe_attachment:
                rows = probe(db, probe_tokens, coverage_mode=attachment_coverage)
            else:
                rows = probe(db, probe_tokens)
        except Exception:
            logger.exception("AND probe %s failed", probe.__name__)
            continue
        if len(rows) >= AND_MODE_LIMIT or getattr(rows, "truncated", False):
            # The probe's LIMIT bound: rows beyond the cap were cut, so any
            # "word X is absent" claim over this type is incomplete. A probe
            # can also self-report (`_ProbeRows.truncated`) when a merged
            # source hit its cap but dedup shrank the list back under it.
            truncated_types.update(r.entity_type for r in rows)
        hits.extend(rows)

    # No intersection → offer fuzzy "did you mean" neighbours (union across tokens,
    # deduped, capped). Best-effort; pg_trgm absence / probe error never fails AND.
    alternatives: list[ResolvedEntity] = []
    if not hits:
        seen: set[tuple[str, str]] = set()
        scope = allowed if allowed is not None else _ALL_TRGM_TYPES
        for tok in clean_tokens:
            try:
                for h in _trgm_lookup(db, tok, scope):
                    if (h.similarity or 0.0) < SUGGEST_FLOOR:
                        continue
                    key = (h.entity_type, h.canonical_code or "")
                    if key in seen:
                        continue
                    seen.add(key)
                    alternatives.append(h)
            except Exception:
                logger.exception("AND-mode alternatives trgm lookup failed for token=%s", tok)
        alternatives.sort(key=lambda e: (bool(e.display.get("is_variant")), e.similarity or 0.0), reverse=True)
        alternatives = alternatives[:_ALTERNATIVES_CAP]

    blocked = _attach_company_info(db, list(hits) + list(alternatives))
    _attach_brand_info(db, list(hits) + list(alternatives))
    if blocked:
        hits = [m for m in hits if (m.entity_type, str(m.uuid)) not in blocked]
        alternatives = [a for a in alternatives if (a.entity_type, str(a.uuid)) not in blocked]

    elapsed = (time.perf_counter() - t0) * 1000.0
    return IntersectionResolutionResult(
        tokens=clean_tokens,
        intersection=hits,
        elapsed_ms=elapsed,
        alternatives=alternatives,
        truncated_entity_types=sorted(truncated_types),
    )


# --------------------------------------------------------------------------- #
# Company attribution + isolation
# --------------------------------------------------------------------------- #
# Resolver entity types whose rows carry a company. `attachment_type` and `form`
# are global and are deliberately absent; `attachment` is company-SHARED, so its
# company_id is legitimately NULL for form/entity files. Types not listed here
# come back with company_id=None and are never scope-gated, which a caller reads
# as "not company-partitioned".
def _company_scoped_models() -> dict[str, Any]:
    """entity_type -> model, for the company-scoped types the resolver can emit.

    Imported lazily (module import order) and rebuilt cheaply; the caller does at
    most one query per entity type actually present in a result.
    """
    from app.models.certificate import Certificate
    from app.models.inventory import Warehouse
    from app.models.marketing import Promotion
    from app.models.order import Customer, Order, Transporter
    from app.models.procurement import (
        InboundShipment,
        PickingHeader,
        SPOAllocation,
        Supplier,
    )
    from app.models.product import Product
    from app.models.product_set import ProductSet
    from app.models.resources import Attachment

    return {
        "product": Product,
        "customer_order": Order,
        "customer": Customer,
        "transporter": Transporter,
        "supplier": Supplier,
        "inbound_shipment": InboundShipment,
        "spo_allocation": SPOAllocation,
        "grn": PickingHeader,
        "warehouse": Warehouse,
        "promotion": Promotion,
        "certificate": Certificate,
        "attachment": Attachment,
        "product_set": ProductSet,
    }


def _company_scope_sql(db: Session, column: str = "company_id") -> tuple[str, dict[str, Any]]:
    """AND-fragment restricting a RAW-SQL probe to the caller's company scope.

    The isolation filter is a ``do_orm_execute`` event, so it only sees ORM
    statements. Every ``db.execute(text(...))`` probe in this module is invisible
    to it: without this clause a trigram probe returns another company's rows
    while the exact ORM probe beside it correctly hides them - the same request
    isolating on one tier and leaking on the next.

    Mirrors ``build_company_predicate`` for an OWNED table. Filtering in SQL (not
    on the result list) also keeps each probe's LIMIT honest: post-filtering lets
    out-of-scope rows eat the 15 trigram slots and starve the in-scope candidates.
    """
    scope = get_company_scope(db)
    if scope is None:  # no contact identity → all companies (backward-compat)
        return "", {}
    if not isinstance(scope, frozenset) or not scope:
        # UNSET (request-entry resolver never ran) or a contact with no company
        # membership. Both are fail-closed.
        return " AND FALSE", {}
    return (
        f" AND {column}::text = ANY(:__company_ids)",
        {"__company_ids": [str(c) for c in scope]},
    )


def _scope_allows(scope: Any, company_id: Optional[str], *, shared: bool) -> bool:
    """Python mirror of ``build_company_predicate`` for one already-read row."""
    if scope is None:
        return True
    if scope is UNSET or not scope:
        return shared and company_id is None
    if company_id is None:
        return shared
    return str(company_id) in scope


def _attach_company_info(db: Session, matches: list[ResolvedEntity]) -> set[tuple[str, str]]:
    """Stamp ``company_id`` / ``company_name`` / ``company_code`` on every
    company-scoped match, and report the ones this caller's company scope must not see.

    Attribution and isolation are one pass because they need the same fact from
    opposite sides of the isolation filter:

    * Attribution reads the owner with RAW SQL so it BYPASSES the filter. An ORM
      read returns no row for another company's match and leaves ``company_id``
      None - indistinguishable from a legitimately shared row, so the caller
      reads a cross-company leak as "global product".
    * Isolation then applies the same predicate in Python, so a row that only
      reached us through a raw-SQL probe (trigram, embedding, variant graph) is
      dropped exactly as the ORM tiers already drop it. ``_company_scope_sql``
      already filters the probes that can carry the clause; this is the backstop
      that holds for the probes that cannot (embedding hits come from
      ``embedding_chunks``, which has no company column) and for any raw probe
      added later.

    Done as a post-pass rather than inside each of the ~20 per-entity resolvers:
    those select explicit column tuples, so threading a company column through
    each one would be twenty chances to miss one and silently emit an
    unattributed - and unfiltered - match.

    Attribution stays best-effort (a failed lookup must never take down a
    resolution that otherwise succeeded), but isolation is fail-closed: when the
    owner lookup fails under a real scope, every row in that group is blocked.

    Returns the blocked ``(entity_type, uuid)`` pairs; callers drop them from
    ``matches`` / ``alternatives`` / ``intersection``.
    """
    blocked: set[tuple[str, str]] = set()
    if not matches:
        return blocked

    by_type: dict[str, list[ResolvedEntity]] = {}
    for m in matches:
        if m.uuid:
            by_type.setdefault(m.entity_type, []).append(m)
    if not by_type:
        return blocked

    scope = get_company_scope(db)
    models = _company_scoped_models()
    company_ids: set[str] = set()
    pending: list[tuple[ResolvedEntity, str]] = []

    for entity_type, group in by_type.items():
        model = models.get(entity_type)
        if model is None:
            continue  # global / unpartitioned type - leave company_id None, never gate
        shared = bool(getattr(model, "__company_shared__", False))
        try:
            # SAVEPOINT: this lookup is best-effort, and a failed statement
            # otherwise leaves the whole transaction aborted - every later
            # query in the request would die on InFailedSqlTransaction.
            with db.begin_nested():
                rows = db.execute(
                    text(
                        f"SELECT id::text AS id, company_id::text AS company_id "  # noqa: S608 - table name from the model registry
                        # `fullname`, not `__tablename__`: a schema-qualified model would
                        # otherwise emit its bare name, and seven of those name a CORE table
                        # too (ADR-0011). No projects model reaches here today; the day one
                        # does, this reads the right rows rather than another module's.
                        f"FROM {model.__table__.fullname} WHERE id::text = ANY(:ids)"
                    ),
                    {"ids": [str(m.uuid) for m in group]},
                ).all()
        except Exception:  # noqa: BLE001 - attribution is additive, never fatal
            logger.exception("company attribution lookup failed for %s", entity_type)
            if not _scope_allows(scope, None, shared=shared):
                # Cannot prove these rows are in scope → do not emit them.
                blocked.update((m.entity_type, str(m.uuid)) for m in group)
            continue
        owner = {r.id: r.company_id for r in rows}
        for m in group:
            cid = owner.get(str(m.uuid))
            if cid:
                m.company_id = cid
                company_ids.add(cid)
                pending.append((m, cid))
            if not _scope_allows(scope, cid, shared=shared):
                blocked.add((m.entity_type, str(m.uuid)))

    if not company_ids:
        return blocked
    try:
        from app.models.company import Company

        names = {
            str(cid): (name, code)
            for cid, name, code in db.query(Company.id, Company.name, Company.code)
            .filter(Company.id.in_(company_ids))
            .all()
        }
    except Exception:  # noqa: BLE001 - an id with no name is still actionable
        logger.exception("company name lookup failed")
        return blocked
    for match, cid in pending:
        name, code = names.get(cid, (None, None))
        match.company_name = name
        match.company_code = code
    return blocked


def fetch_product_brands(db: Session, product_ids: Iterable[str]) -> dict[str, Optional[dict[str, Any]]]:
    """product uuid -> its brand payload (or None when the product has no brand).

    Read with RAW SQL for the same reason `_attach_company_info` does: the
    company isolation filter is an ORM event, and these ids have ALREADY passed
    (or are about to pass) that filter as part of the match they describe.
    Re-reading them through the ORM would silently drop the brand of a row a
    raw-SQL probe legitimately surfaced.

    Only ids present in the returned map were actually read; a caller that finds
    an id missing should leave the match alone rather than assert "no brand".
    """
    ids = [str(p) for p in product_ids if p]
    if not ids:
        return {}
    rows = db.execute(
        text(
            "SELECT p.id::text AS id, b.id::text AS brand_id, b.brand_code, b.brand_name "
            "FROM products p LEFT JOIN brands b ON b.id = p.brand_id "
            "WHERE p.id::text = ANY(:ids)"
        ),
        {"ids": ids},
    ).all()
    out: dict[str, Optional[dict[str, Any]]] = {}
    for r in rows:
        out[r.id] = (
            {
                "brand_id": r.brand_id,
                "brand_code": r.brand_code,
                "brand_name": r.brand_name,
            }
            if r.brand_id
            else None
        )
    return out


def _attach_brand_info(db: Session, matches: list[ResolvedEntity]) -> None:
    """Stamp `display.brand` on every product match, whatever path found it.

    Brand and company are different axes: Cabana is a BRAND under the Sorento
    COMPANY, so `company_name` cannot tell a Cabana product from any other
    Sorento one. Downstream routing that keys on brand had nothing to read on
    the code-lookup path, which is the common one.

    Post-pass rather than per-probe for the same reason company attribution is:
    the probes each select an explicit column tuple, so threading a brand join
    through every one of them is a fresh chance to miss one and emit a product
    that silently claims no brand.

    `brand` is set to None (rather than omitted) when the product genuinely has
    none, so a consumer can tell "this product has no brand" from "this match
    path forgot to send one". Best-effort: a failed lookup leaves the matches
    untouched and never fails a resolution that otherwise succeeded.
    """
    products = [m for m in matches if m.entity_type == "product" and m.uuid]
    if not products:
        return
    try:
        brands = fetch_product_brands(db, [str(m.uuid) for m in products])
    except Exception:  # noqa: BLE001 - brand is additive, never fatal
        logger.exception("brand attribution lookup failed")
        return
    for m in products:
        key = str(m.uuid)
        if key not in brands:
            continue
        m.display["brand"] = brands[key]


def _apply_company_scope(db: Session, resolutions: list[TokenResolution]) -> None:
    """Attribute + company-scope every match and alternative, in place.

    A suggestion the contact's company does not own is a leak wearing a helpful
    hat, so `alternatives` are gated on the same boundary as `matches`. Dropping
    a candidate can also un-ambiguate a token (two candidates, one in scope), so
    the stored `ambiguous` flag is recomputed; `resolved` / `unresolved_tokens`
    are properties and follow automatically.
    """
    everything = [m for tr in resolutions for m in tr.matches] + [
        a for tr in resolutions for a in tr.alternatives
    ]
    blocked = _attach_company_info(db, everything)
    _attach_brand_info(db, everything)
    if not blocked:
        return
    for tr in resolutions:
        tr.matches = [m for m in tr.matches if (m.entity_type, str(m.uuid)) not in blocked]
        tr.alternatives = [
            a for a in tr.alternatives if (a.entity_type, str(a.uuid)) not in blocked
        ]
        if len(tr.matches) <= 1:
            tr.ambiguous = False


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def _probe_project(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Resolve a project by its CODE or its exact title (AC-K1).

    Both, because people refer to a pursuit either way: office staff say "PRJ-000142", a
    salesperson says "Residensi Damai Phase 1". The code is the canonical_code either way, so
    whatever the caller typed, the agent gets back the identifier the tools accept.

    Exact (whitespace-insensitive, case-insensitive) only. Tier 2 owns partial matching, and
    a project list is exactly where a loose match is dangerous: two phases of one masterplan
    have near-identical titles, and silently picking one would answer a question about the
    wrong pursuit.
    """
    from app.models.projects import Project

    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    norm_to_token = {_strip_all_ws(t.lower()): t for t in tokens}
    keys = list(norm_to_token.keys())

    rows = (
        db.query(
            Project.id,
            Project.project_code,
            Project.title,
            Project.outcome,
            Project.owner_user_id,
        )
        .filter(
            or_(
                _ws_insensitive_lower(Project.project_code).in_(keys),
                _ws_insensitive_lower(Project.title).in_(keys),
            )
        )
        .all()
    )
    for pid, code, title, outcome, owner_user_id in rows:
        by_code = _strip_all_ws(str(code or "").lower())
        by_title = _strip_all_ws(str(title or "").lower())
        token = norm_to_token.get(by_code) or norm_to_token.get(by_title)
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="project",
                canonical_code=code,
                uuid=str(pid) if pid else None,
                match_field="project_code" if norm_to_token.get(by_code) else "title",
                display={
                    "title": title,
                    "outcome": outcome,
                    "owner_user_id": owner_user_id,
                },
            )
        )
    return result


def _probe_project_party(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Resolve a developer / architect / main contractor by name (AC-K1).

    One probe for all party types rather than three: they share a table and a naming style,
    and the party TYPE is returned in the display so the caller can tell "Damai Land the
    developer" from a contractor of the same group. Filtering by type here would mean three
    near-identical probes and a caller that has to know which one to ask.

    The party name IS the canonical code -- these rows have no business code, which is the
    honest answer for a record that exists because somebody typed a company name.
    """
    from app.models.projects import ProjectParty

    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    if not tokens:
        return result
    norm_to_token = {_strip_all_ws(t.lower()): t for t in tokens}

    rows = (
        db.query(
            ProjectParty.id,
            ProjectParty.name,
            ProjectParty.party_type,
            ProjectParty.is_active,
        )
        .filter(_ws_insensitive_lower(ProjectParty.name).in_(list(norm_to_token.keys())))
        .all()
    )
    for party_id, name, party_type, is_active in rows:
        token = norm_to_token.get(_strip_all_ws(str(name or "").lower()))
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="project_party",
                canonical_code=name,
                uuid=str(party_id) if party_id else None,
                match_field="name",
                display={
                    "party_type": party_type,
                    "is_active": bool(is_active) if is_active is not None else True,
                },
            )
        )
    return result


def _probe_user(db: Session, tokens: list[str]) -> dict[str, list[ResolvedEntity]]:
    """Resolve a colleague by full name or work email.

    This exists because several tools filter on a USER uuid -- `owner_user_ids` on the project
    list is the first, and "what is Ali working on" is the most natural way anyone asks for it.
    Without a probe the model has no path from the name it was given to the uuid the filter
    wants, so the filter is advertised in the tool description and unreachable in practice.

    Deliberately EXACT (whitespace and case insensitive) and active-only:

    * exact, because a fuzzy staff-name match is how one salesperson's numbers get reported as
      another's, and there is no code on a user row to disambiguate with afterwards;
    * active-only, because answering "Ali has 12 live projects" about somebody who left the
      company is worse than answering "I could not find Ali".

    Names are not unique, so several matches for one token is a normal outcome; the caller
    decides (the dispatcher passes every uuid, which reads as "either of the two Alis").
    """
    from app.models.user import User, UserStatus

    result: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    # A 1-2 character token is never a person; it is an initial or a unit, and matching it
    # would put a user candidate on half the tokens in a sentence.
    norm_to_token = {
        _strip_all_ws(t.lower()): t for t in tokens if len(_strip_all_ws(t)) >= 3
    }
    if not norm_to_token:
        return result
    keys = list(norm_to_token.keys())

    rows = (
        db.query(User.id, User.name, User.email)
        .filter(
            # Plain equality, never `upper(status)`: the live column is a Postgres ENUM while
            # the model declares String, so a function call on it fails on the real database
            # and passes nowhere else.
            User.status == UserStatus.ACTIVE.value,
            or_(
                _ws_insensitive_lower(User.name).in_(keys),
                _ws_insensitive_lower(User.email).in_(keys),
            ),
        )
        .all()
    )
    for user_id, name, email in rows:
        norm_name = _strip_all_ws(str(name or "").lower())
        norm_email = _strip_all_ws(str(email or "").lower())
        token = norm_to_token.get(norm_name) or norm_to_token.get(norm_email)
        if not token:
            continue
        result[token].append(
            ResolvedEntity(
                entity_type="user",
                canonical_code=name or email,
                uuid=str(user_id) if user_id else None,
                match_field="name" if norm_to_token.get(norm_name) == token else "email",
                display={"name": name, "email": email},
            )
        )
    return result


_TIER1_PROBES: tuple[tuple[Callable[[Session, list[str]], dict[str, list[ResolvedEntity]]], frozenset[str]], ...] = (
    (_probe_product, frozenset({"product"})),
    (_probe_product_set, frozenset({"product_set"})),
    (_probe_customer_order, frozenset({"customer_order"})),
    (_probe_inbound_shipment, frozenset({"inbound_shipment"})),
    (_probe_spo, frozenset({"spo_allocation"})),
    (_probe_grn, frozenset({"grn"})),
    (_probe_warehouse, frozenset({"warehouse"})),
    (_probe_supplier, frozenset({"supplier"})),
    (_probe_promotion, frozenset({"promotion"})),
    (_probe_transporter, frozenset({"transporter"})),
    (_probe_customer, frozenset({"customer"})),
    (_probe_customer_debtor_name, frozenset({"customer"})),
    (_probe_attachment, frozenset({"attachment"})),
    (_probe_attachment_type, frozenset({"attachment_type"})),
    (_probe_project, frozenset({"project"})),
    (_probe_project_party, frozenset({"project_party"})),
    (_probe_user, frozenset({"user"})),
    (_probe_certificate, frozenset({"certificate"})),
)


class EntityPinMismatch(Exception):
    """A caller-supplied `entity_pins` entry does not resolve to exactly one
    of the token's own SCOPED matches.

    ONE rule, no exceptions: a pin whose uuid is not among the token's matches
    AFTER company-scope filtering raises. This covers every failure shape the
    same way - a stale/bogus uuid, a uuid belonging to a DIFFERENT token, a
    real row the caller's own company scope cannot see (LESSONS section 61c:
    a customer/product code is unique only PER COMPANY), an unparseable uuid,
    a blank pin value, and an `entity_pins` key that names no token in this
    request at all. Earlier drafts tried to tell "real row, wrong scope" apart
    from "bogus uuid" by re-probing the database with company scope widened to
    None - that path is gone: it only ever checked Tier-1 (so it was WRONG for
    a pin on a Tier-2/3 match), it was a hand-rolled reimplementation of the
    scope machinery `company_scope`/`set_company_scope` already owns (with an
    autoflush-stamping hazard from mutating session-level scope mid-request),
    and it opened a company-existence oracle: a caller could learn "this uuid
    exists in SOME company" from a 200 it should never have gotten.

    Silently ignoring a pin - falling back to plain token matching, or just
    dropping an unrecognised key - is exactly the failure mode this feature
    exists to close (n8n exec 14659385, 14661446: a picked customer/product
    re-opened as ambiguous because the caller re-derived from the bare code
    instead of the uuid it had already pinned).
    """

    def __init__(self, token: str, pinned_uuid: str) -> None:
        self.token = token
        self.pinned_uuid = pinned_uuid
        super().__init__(
            f"entity_pins uuid {pinned_uuid!r} for token {token!r} is not "
            "among that token's own scoped candidate matches"
        )


def _canonical_pin_uuid(value: Any) -> Optional[str]:
    """`str(uuid.UUID(value))` - accepts hyphenless / braced / mixed-case forms
    and normalises to lowercase-hyphenated. `None` on anything unparseable."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _pin_key_norm(token: str) -> str:
    return (token or "").strip().lower()


def _apply_entity_pins(
    resolutions: list[TokenResolution], entity_pins: dict[str, str]
) -> None:
    """Narrow each pinned token to exactly its pinned uuid, in place. Called
    AFTER `_apply_company_scope`, so every check below is against the token's
    own SCOPED matches.

    ONE rule: a pin's uuid must be exactly one of the token's scoped matches,
    or the whole call raises `EntityPinMismatch` - see that class for why a
    bogus uuid and a real-but-out-of-scope uuid are deliberately NOT told
    apart. Same treatment for a blank pin value and an unparseable uuid.

    Key matching is case-insensitive on the (already-stripped, see
    `references._resolve_input`) token. Every `entity_pins` key must bind to
    a token actually present in `resolutions`; a key that binds to nothing -
    a typo, a stale key from an earlier turn - raises too, rather than being
    silently ignored.
    """
    by_key: dict[str, tuple[str, str]] = {
        _pin_key_norm(key): (key, value) for key, value in entity_pins.items()
    }
    consumed: set[str] = set()

    for tr in resolutions:
        norm_tok = _pin_key_norm(tr.token)
        entry = by_key.get(norm_tok)
        if entry is None:
            continue
        orig_key, raw_pin = entry
        consumed.add(norm_tok)

        pin_norm = _canonical_pin_uuid((raw_pin or "").strip())
        if pin_norm is None:
            raise EntityPinMismatch(orig_key, raw_pin)

        pinned = next(
            (m for m in tr.matches if _canonical_pin_uuid(m.uuid) == pin_norm),
            None,
        )
        if pinned is None:
            raise EntityPinMismatch(orig_key, raw_pin)
        tr.matches = [pinned]
        tr.ambiguous = False

    for norm_key, (orig_key, raw_pin) in by_key.items():
        if norm_key not in consumed:
            raise EntityPinMismatch(orig_key, raw_pin)


# --------------------------------------------------------------------------- #
# Per-request memo
# --------------------------------------------------------------------------- #
# One resolve request can ask the same question of the database several times: the
# cross-type expansion re-runs the Tier-1 / Tier-2 probes the tiers above it just ran,
# and `references._resolve_input`'s all-types fallback runs a token the primary pass
# could not resolve through the whole pipeline again - embedding it a second time, an
# OpenAI round trip. Measured (CHAT-SLOW-MISS, 4 Oct 2026): one missing code in a
# three-code stock ask was probed three times and embedded twice in one request.
#
# The memo holds each (probe, token) answer and each query vector for the life of ONE
# request (`resolve_memo()`), and hands back copies: callers mutate what they get
# (`match_tier`, company attribution). It never outlives the request, so it cannot
# serve a stale row.
_RESOLVE_MEMO: ContextVar[Optional[dict]] = ContextVar("entity_resolver_memo", default=None)


class resolve_memo:
    """Share probe answers across every resolve pass inside this block. Nests."""

    def __enter__(self):
        self._token = _RESOLVE_MEMO.set({}) if _RESOLVE_MEMO.get() is None else None
        return self

    def __exit__(self, *exc):
        if self._token is not None:
            _RESOLVE_MEMO.reset(self._token)
        return False


def _with_resolve_memo(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with resolve_memo():
            return fn(*args, **kwargs)

    return wrapper


def _memo_key(probe, token: str, regions) -> tuple:
    return (probe, token, None if regions is None else frozenset(regions))


def _memo_tier1(probe, db: Session, tokens: list[str], regions) -> dict[str, list[ResolvedEntity]]:
    """`probe(db, tokens)` (Tier-1, batched), asking the database only for tokens it
    has not already answered for in this request."""
    memo = _RESOLVE_MEMO.get()
    if memo is None:
        return _call_tier1(probe, db, tokens, regions)
    todo = [t for t in tokens if _memo_key(probe, t, regions) not in memo]
    if todo:
        fresh = _call_tier1(probe, db, todo, regions)
        # A batch maps its rows back by NORMALISED code, so of two spellings of one code
        # ("SRT-5764", "srt5764") only one gets the row. Asked alone, each would find it:
        # such a token's batched answer is not its answer, so it is not remembered.
        folded = collections.Counter(_alnum_casefold(t) for t in todo)
        for t in todo:
            if folded[_alnum_casefold(t)] == 1:
                memo[_memo_key(probe, t, regions)] = copy.deepcopy(fresh.get(t, []))
    out: dict[str, list[ResolvedEntity]] = {}
    for t in tokens:
        key = _memo_key(probe, t, regions)
        out[t] = copy.deepcopy(memo[key]) if key in memo else fresh.get(t, [])
    return out


def _call_tier1(probe, db: Session, tokens: list[str], regions) -> dict[str, list[ResolvedEntity]]:
    if probe is _probe_inbound_shipment:
        return probe(db, tokens, regions=regions)
    return probe(db, tokens)


def _memo_tier2(probe, db: Session, token: str, regions) -> list[ResolvedEntity]:
    """`probe(db, token)` (Tier-2, one token), once per request."""
    memo = _RESOLVE_MEMO.get()
    key = _memo_key(probe, token, regions)
    if memo is None or key not in memo:
        hits = (
            probe(db, token, regions=regions)
            if probe is _prefix_probe_inbound_shipment
            else probe(db, token)
        )
        if memo is None:
            return hits
        memo[key] = copy.deepcopy(hits)
    return copy.deepcopy(memo[key])


def _memo_embedding(token: str, embed) -> list[float]:
    """The query vector for `token`, embedded once per request."""
    memo = _RESOLVE_MEMO.get()
    key = ("embedding", token)
    if memo is None:
        return embed([token])[0]
    if key not in memo:
        memo[key] = list(embed([token])[0])
    return memo[key]


@_with_resolve_memo
def resolve_references(
    db: Session,
    query_or_tokens: str | list[str],
    *,
    max_candidates: int = 8,
    enable_prefix_fallback: bool = True,
    enable_embedding_fallback: bool = True,
    allowed_entity_types: Optional[Iterable[str]] = None,
    cross_type_expand: bool = False,
    domain_hint: Optional[str] = None,
    entity_pins: Optional[dict[str, str]] = None,
    raw_tokens: Optional[list[str]] = None,
    regions=None,
) -> ResolutionResult:
    """Main entry point.

    `regions` (a contact's packing list regions) hides inbound shipments outside them from the
    exact and prefix probes; None = no filter.

    Runs three tiers in order, stopping per-token as soon as a tier yields a match:

      Tier 1 - exact case-insensitive lookup on all code fields.
      Tier 2 - prefix / substring ILIKE on the same code fields (handles partial codes).
      Tier 3 - embedding vector search over primary entities (semantic last resort).

    Tier 2/3 results with ambiguity (multiple candidates, or low confidence gap) are
    flagged `ambiguous=True` so the LLM asks the user to pick rather than guessing.

    When `allowed_entity_types` is set, probes whose output type is outside the set are
    skipped, and Tier-3 vector search filters to allowed source_types. Per-list-tool
    callers pass the types they can filter on so resolution doesn't return entities the
    caller has nowhere to send.

    `entity_pins` (token -> uuid) narrows an already-disambiguated token to
    exactly that row instead of re-deriving from the bare code - see
    `_apply_entity_pins` (run AFTER company-scope filtering, below) for the
    full contract. A uuid not among that token's SCOPED matches always raises
    `EntityPinMismatch` - a bogus uuid and a real-but-out-of-scope uuid are
    NOT distinguished, both are a 400 to the caller. Every `entity_pins` key
    must bind to a token actually being resolved, or the whole call raises.

    `raw_tokens` (positionally parallel to `query_or_tokens` when the latter
    is a list) carries the pre-fold raw text behind each token, e.g. n8n folds
    "SRTWB8004 BASIN TAP" into one token "SRTWB8004BASINTAP" before calling
    resolve. A token that is still matchless after Tier 2 gets one more try:
    the leading code-shaped word of its raw text is exact-probed against
    products only (`match_tier="head_code"` on a hit). Ignored when the two
    lists have different lengths, or when `query_or_tokens` is a plain string.
    """
    t0 = time.perf_counter()
    raw_query: Optional[str] = None
    # {token: pre-fold raw text} for the head-code retry, built by zipping
    # `query_or_tokens` with `raw_tokens` BEFORE the strip/empty-filter/cap
    # below, so positional alignment survives it. Empty (never populated) when
    # `query_or_tokens` is a plain string or the two lists' lengths differ.
    raw_by_token: dict[str, str] = {}
    if isinstance(query_or_tokens, list):
        paired_raw = (
            raw_tokens
            if isinstance(raw_tokens, list) and len(raw_tokens) == len(query_or_tokens)
            else None
        )
        tokens = []
        for idx, t in enumerate(query_or_tokens):
            s = (t or "").strip()
            if not s:
                continue
            tokens.append(s)
            if paired_raw is not None:
                raw_by_token[s] = paired_raw[idx]
        tokens = tokens[:max_candidates]
        if paired_raw is not None:
            raw_by_token = {t: raw_by_token[t] for t in tokens}
    else:
        raw_query = query_or_tokens or ""
        tokens = extract_candidate_tokens(raw_query, max_candidates=max_candidates)

    # Positional pairing: when caller passed parallel lists of tokens + entity types
    # of equal length, each token resolves ONLY against its paired type. Otherwise
    # `allowed` is treated as the legacy global type-set filter.
    pair_map = _build_token_type_map(tokens, allowed_entity_types)
    if pair_map is not None:
        allowed: Optional[frozenset[str]] = frozenset(pair_map.values())
    elif allowed_entity_types is not None:
        # Apply one-to-many expansion (brand / category → product + promotion).
        allowed = _expand_entity_types(allowed_entity_types, domain_hint=domain_hint)
    else:
        allowed = None

    def _types_for(tok: str) -> Optional[frozenset[str]]:
        if pair_map is not None:
            paired = pair_map.get(tok)
            return frozenset({paired}) if paired else frozenset()
        return allowed

    # D10b (owner console pass, 8 Sep 2026, turn 69d9900e): the caller's OWN requested
    # types, canonicalized but NOT expanded. `allowed` above already widens "product" to
    # also reach `product_set` (a flyer/set code is a legitimate product-domain hit in
    # general, and n8n relies on that), which is exactly why a token that matched ONLY
    # `product_set` still counts as `resolved` and never reached the alternatives search
    # below - the caller asked for "product", not "product or product_set", and has no way
    # to tell a set code from an absence. Compared against this NARROWER set, not `allowed`.
    caller_types: Optional[frozenset[str]] = (
        frozenset(_canonical_entity_type(t) for t in allowed_entity_types if t)
        if allowed_entity_types is not None
        else None
    )

    # `product_attachment` is the ONE domain this filters: a customer asking for a
    # document ABOUT a product must never land on an internal SCM document type
    # (Shipment Line Photo, Proforma Invoice, ...) that happens to share a substring
    # with the word they used ("photo"). Opt-in by domain, same pattern as
    # `attachment_coverage` in `resolve_references_intersection` - every other
    # caller's attachment_type resolution (resource_attachment browsing, admin
    # search, ...) is unaffected. Computed once, lazily, only if a Tier-2
    # attachment_type candidate is actually produced.
    _scope_attachment_types = (domain_hint or "").strip().lower() == "product_attachment"
    _product_attachment_type_id_cache: frozenset[str] | None = None

    # ----- Tier 1: exact -----
    per_token: dict[str, list[ResolvedEntity]] = {t: [] for t in tokens}
    ambiguous_tokens: set[str] = set()
    if tokens:
        for probe, produces in _TIER1_PROBES:
            if allowed is not None and produces.isdisjoint(allowed):
                continue
            if pair_map is not None:
                probe_tokens = [t for t in tokens if pair_map.get(t) in produces]
                if not probe_tokens:
                    continue
            else:
                probe_tokens = tokens
            try:
                hits = _memo_tier1(probe, db, probe_tokens, regions)
            except Exception:
                logger.exception("Tier-1 probe %s failed", probe.__name__)
                continue
            for tok, matches in hits.items():
                per_token[tok].extend(matches)

        # One certificate number can be approved under TWO schemes (`04124FC`
        # exists under both PPS and SPAN, with different expiries). Multiple
        # tier-1 certificate hits are therefore a real disambiguation, not a
        # data error: flag the token so the agent asks which scheme instead of
        # answering from whichever row the database returned first.
        for tok in tokens:
            if len([m for m in per_token[tok] if m.entity_type == "certificate"]) > 1:
                ambiguous_tokens.add(tok)

    # ----- Tier 2: prefix / substring fallback (only for tokens still empty) -----
    if enable_prefix_fallback:
        for tok in tokens:
            tok_allowed = _types_for(tok)
            if tok_allowed is not None and not tok_allowed:
                continue
            # Product variant expansion: Tier 1 exact returns only the row whose
            # code equals the token (e.g. SRTKS6145). Variants like SRTKS6145-NEW
            # / -UF / -OLD share a base stem and never match exact. When Tier 1
            # already produced a product hit, additionally probe prefix on the
            # product table so siblings surface alongside the exact match. Other
            # entity types keep strict single-hit semantics - variant siblings
            # are a product-domain concept (SKU suffixes); customers / orders /
            # promotions don't have the same family pattern.
            existing_product_hit = any(
                m.entity_type == "product" for m in per_token[tok]
            )
            if existing_product_hit and (tok_allowed is None or "product" in tok_allowed):
                seen_uuids = {m.uuid for m in per_token[tok] if m.uuid}
                for variant in _prefix_probe_product(db, tok):
                    if variant.uuid and variant.uuid in seen_uuids:
                        continue
                    per_token[tok].append(variant)
                    if variant.uuid:
                        seen_uuids.add(variant.uuid)
                # Multi-row product result must flag ambiguous so the LLM asks
                # the user to pick / treats them as a set rather than picking
                # the first match silently.
                if len([m for m in per_token[tok] if m.entity_type == "product"]) > 1:
                    ambiguous_tokens.add(tok)
            if per_token[tok]:
                continue
            candidates = _tier2_fuzzy_lookup(
                db, tok, allowed_entity_types=tok_allowed, regions=regions
            )
            if _scope_attachment_types and any(
                c.entity_type == "attachment_type" for c in candidates
            ):
                if _product_attachment_type_id_cache is None:
                    _product_attachment_type_id_cache = _product_attachment_type_ids(db)
                candidates = [
                    c
                    for c in candidates
                    if c.entity_type != "attachment_type"
                    or c.uuid in _product_attachment_type_id_cache
                ]
            if not candidates:
                continue
            if len(candidates) == 1:
                per_token[tok] = candidates
                continue
            # 2+ matches - let the LLM ask the user to pick, cap surface size.
            # Per-type balanced cap: when several entity types matched (e.g.
            # `kitchen sink` hits 20 products + 6 promotions), a flat top-N
            # would crowd out the smaller types entirely. Allocate the budget
            # across active types so every type that matched gets seen.
            per_type: dict[str, list[ResolvedEntity]] = {}
            for c in candidates:
                per_type.setdefault(c.entity_type, []).append(c)
            n_types = len(per_type)
            per_type_cap = max(1, PREFIX_LIMIT // max(1, n_types))
            balanced: list[ResolvedEntity] = []
            for items in per_type.values():
                balanced.extend(items[:per_type_cap])
            per_token[tok] = balanced[:PREFIX_LIMIT]
            ambiguous_tokens.add(tok)

    # ----- Head-code retry (product tokens only, still matchless) -----
    # n8n folds "SRTWB8004 BASIN TAP" into ONE token "SRTWB8004BASINTAP" before
    # calling resolve, so Tier 1/2 above see only the folded form and miss the
    # code inside it (a SPACED catalogue code like "SRTWB7299-WALL HUNG" still
    # exact-hits Tier 1 above, since both sides are dash/ws-stripped, so it
    # never reaches here). `raw_by_token` carries the pre-fold text; when the
    # folded token is still empty, pull a leading code-shaped word out of that
    # raw text and exact-probe it against products only. See
    # PLAN-resolver-head-code-retry.md.
    if raw_by_token:
        for tok in tokens:
            if per_token[tok] or tok in ambiguous_tokens:
                continue
            tok_allowed = _types_for(tok)
            if tok_allowed is not None and "product" not in tok_allowed:
                continue
            raw = raw_by_token.get(tok)
            if not raw:
                continue
            raw_stripped = raw.strip()
            if not raw_stripped or " " not in raw_stripped or raw_stripped == tok:
                continue
            m = _HEAD_CODE_RE.match(raw_stripped)
            if not m:
                continue
            head = m.group(0)
            if _strip_all_ws(head).lower() == _strip_all_ws(raw).lower():
                # The head IS the whole raw text - that lookup already ran
                # (as the folded token) and missed.
                continue
            try:
                hits = _probe_product(db, [head]).get(head, [])
            except Exception:
                logger.exception("Head-code retry probe failed for token=%s head=%s", tok, head)
                continue
            if not hits:
                continue
            for h in hits:
                h.match_tier = "head_code"
            per_token[tok] = hits
            if len(hits) > 1:
                ambiguous_tokens.add(tok)

    # Snapshot which tokens already had matches BEFORE cross-type expansion -
    # used below to flag tokens that grew into multi-type candidate sets so the
    # LLM disambiguates instead of picking the first match.
    pre_expand_type_count: dict[str, int] = {
        tok: len({m.entity_type for m in per_token[tok]}) for tok in tokens
    }

    # ----- Cross-type expansion (fallback discovery mode) -----
    # In `cross_type_expand` mode we keep probing OTHER entity types for tokens
    # that already got a tier-1 / tier-2 hit. The fallback path
    # (`fallback_to_all_types=True`) uses this so a token like "Sorento" which
    # exact-matches a `transporter` code can ALSO surface as a `promotion` /
    # `brand` / `customer` candidate. Without this, the per-token early exit
    # after tier-1 swallows every other type and `fallback_types_found` only
    # reports the first probe that fired.
    if cross_type_expand and tokens:
        for tok in tokens:
            existing_types = {m.entity_type for m in per_token[tok]}
            seen_uuids = {m.uuid for m in per_token[tok] if m.uuid}
            # Tier-1 (exact) across remaining types.
            for probe, produces in _TIER1_PROBES:
                if produces.issubset(existing_types):
                    continue
                if allowed is not None and produces.isdisjoint(allowed):
                    # Respect explicit whitelist when caller still passed one;
                    # fallback path clears `allowed` so this is a no-op there.
                    continue
                try:
                    hits = _memo_tier1(probe, db, [tok], regions).get(tok, [])
                except Exception:
                    logger.exception("Cross-type tier-1 probe %s failed", probe.__name__)
                    continue
                for m in hits:
                    if m.uuid and m.uuid in seen_uuids:
                        continue
                    per_token[tok].append(m)
                    if m.uuid:
                        seen_uuids.add(m.uuid)
                    existing_types.add(m.entity_type)
            # Tier-2 (prefix / substring) across remaining types.
            if enable_prefix_fallback:
                for probe, produces in _TIER2_PROBES:
                    if produces.issubset(existing_types):
                        continue
                    if allowed is not None and produces.isdisjoint(allowed):
                        continue
                    try:
                        hits = _memo_tier2(probe, db, tok, regions)
                    except Exception:
                        logger.exception("Cross-type tier-2 probe %s failed", probe.__name__)
                        continue
                    for m in hits[:PREFIX_LIMIT]:
                        if m.uuid and m.uuid in seen_uuids:
                            continue
                        per_token[tok].append(m)
                        if m.uuid:
                            seen_uuids.add(m.uuid)
                        existing_types.add(m.entity_type)

    # Mark cross-type-expanded tokens ambiguous when expansion grew them from a
    # single-type confident match into a multi-type candidate union. Without
    # this, `TokenResolution.confident_match` would silently pick the first
    # match (e.g. transporter) even though the agent should reroute via the
    # newly-surfaced types (promotion, brand, ...).
    if cross_type_expand:
        for tok in tokens:
            current_types = {m.entity_type for m in per_token[tok]}
            if len(current_types) > 1 and len(current_types) > pre_expand_type_count.get(tok, 0):
                ambiguous_tokens.add(tok)

    # ----- Tier 3: embedding fallback (only for tokens still empty AND not ambiguous) -----
    if enable_embedding_fallback:
        for tok in tokens:
            if per_token[tok] or tok in ambiguous_tokens:
                continue
            tok_allowed = _types_for(tok)
            if tok_allowed is not None and not tok_allowed:
                continue
            hits = _tier3_embedding_lookup(db, tok, allowed_entity_types=tok_allowed)
            if hits:
                per_token[tok] = hits

    # ----- Free-text debtor + transporter scan (when raw query string was given) -----
    # Lets "Find DO for jayson Feb 2026" resolve "jayson" → entity_type=customer, and
    # "transporter Svind gt delivery" resolve "gt delivery" → entity_type=transporter,
    # without explicit markers. Free-word candidates are bulk-queried so the cost
    # stays bounded regardless of how many surface from the phrase.
    # Positional pairing already pins every token to a single type, so the free-word
    # scan would only produce noise - skip it in that mode.
    freeword_resolutions: list[TokenResolution] = []
    if raw_query and pair_map is None and (allowed is None or {"customer", "transporter"} & allowed):
        existing_lower = {t.lower() for t in tokens}
        freeword_candidates = [
            w
            for w in extract_freeword_candidates(raw_query, max_candidates=max_candidates)
            if w.lower() not in existing_lower
        ]
        if freeword_candidates:
            customer_hits: dict[str, list[ResolvedEntity]] = {}
            transporter_hits: dict[str, list[ResolvedEntity]] = {}
            if allowed is None or "customer" in allowed:
                try:
                    customer_hits = _probe_customer_debtor_name(db, freeword_candidates)
                except Exception:
                    logger.exception("Free-word debtor probe failed")
                    customer_hits = {t: [] for t in freeword_candidates}
            if allowed is None or "transporter" in allowed:
                try:
                    transporter_hits = _probe_transporter_freeword(db, freeword_candidates)
                except Exception:
                    logger.exception("Free-word transporter probe failed")
                    transporter_hits = {t: [] for t in freeword_candidates}
            for tok in freeword_candidates:
                matches: list[ResolvedEntity] = []
                matches.extend(customer_hits.get(tok) or [])
                matches.extend(transporter_hits.get(tok) or [])
                if matches:
                    freeword_resolutions.append(TokenResolution(token=tok, matches=matches))

    resolutions = [
        TokenResolution(token=t, matches=per_token[t], ambiguous=(t in ambiguous_tokens))
        for t in tokens
    ]
    resolutions.extend(freeword_resolutions)

    # Fuzzy "did you mean" alternatives for tokens that matched NOTHING - PLUS (D10b) a
    # token whose every match is a type the caller never asked for (`caller_types`, not
    # the internally-widened `allowed`). The latter is the SAME shape as matching nothing:
    # the caller has no use for the row it got and no way to tell that from a genuine miss.
    # Trigram neighbours only (entity-level, no domain-data gate); best-effort so a missing
    # pg_trgm (e.g. sqlite tests) or a probe error never fails resolution. Capped
    # and floored to keep the surface tight and relevant.
    for tr in resolutions:
        if tr.matches and (
            caller_types is None or any(m.entity_type in caller_types for m in tr.matches)
        ):
            continue
        tok_types = _types_for(tr.token)
        if tok_types is not None and not tok_types:
            continue
        try:
            hits = _trgm_lookup(db, tr.token, tok_types if tok_types is not None else _ALL_TRGM_TYPES)
        except Exception:
            logger.exception("resolve alternatives trgm lookup failed for token=%s", tr.token)
            hits = []
        if tr.matches:
            # D10b: a product_set's own MEMBER products are not an alternative to the set
            # code that already matched - they are PART of what the customer already
            # named, not a different thing to try instead. Measured: without this, "did
            # you mean" for SRTWC8610-SH led with its own two members (SRTWCX8610-SH,
            # SRTWCY8610-SH, similarity 0.667) ahead of the sibling codes a customer could
            # actually use (SRTWC8611/8613/8614, similarity 0.571).
            member_uuids = {
                str(member.get("uuid"))
                for m in tr.matches
                for member in (m.display.get("members") or [])
                if isinstance(member, dict) and member.get("uuid")
            }
            if member_uuids:
                hits = [h for h in hits if str(h.uuid) not in member_uuids]
        tr.alternatives = [
            h for h in hits if (h.similarity or 0.0) >= ENTITY_MISS_SUGGEST_FLOOR
        ][:_ALTERNATIVES_CAP]

    _apply_company_scope(db, resolutions)
    if regions is not None:
        # Tier 3 (embedding) and any raw tier are not region aware.
        _drop_hidden_shipments(db, resolutions, regions)

    # Entity pins run LAST, against the token's own SCOPED matches - see
    # `_apply_entity_pins` for the single-rule contract.
    if entity_pins:
        _apply_entity_pins(resolutions, entity_pins)

    final_tokens = list(tokens) + [r.token for r in freeword_resolutions]
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return ResolutionResult(tokens=final_tokens, resolutions=resolutions, elapsed_ms=elapsed_ms)


# --------------------------------------------------------------------------- #
# Single-bag `entities` API for list tools
# --------------------------------------------------------------------------- #
@dataclass
class EntityFilterBuckets:
    """Per-type canonical values + structured echo for `entities: list[str]` callers.

    A list tool collapses customer/product/transporter/order filters into a single
    `entities` param. The resolver decides each input's entity_type; this helper
    packages the result into ready-to-use SQL inputs plus an authoritative echo so
    the agent can surface "what did you actually match" back to the user.
    """

    product_codes: list[str] = field(default_factory=list)
    debtor_names: list[str] = field(default_factory=list)
    customer_codes: list[str] = field(default_factory=list)
    customer_names: list[str] = field(default_factory=list)
    transporter_codes: list[str] = field(default_factory=list)
    transporter_names: list[str] = field(default_factory=list)
    order_numbers: list[str] = field(default_factory=list)
    shipment_numbers: list[str] = field(default_factory=list)
    picking_numbers: list[str] = field(default_factory=list)
    spo_numbers: list[str] = field(default_factory=list)
    supplier_codes: list[str] = field(default_factory=list)
    promotion_ids: list[str] = field(default_factory=list)
    attachment_filenames: list[str] = field(default_factory=list)
    form_codes: list[str] = field(default_factory=list)
    resolved: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def has_resolved_filter(self) -> bool:
        return bool(
            self.product_codes
            or self.debtor_names
            or self.customer_codes
            or self.customer_names
            or self.transporter_codes
            or self.transporter_names
            or self.order_numbers
            or self.shipment_numbers
            or self.picking_numbers
            or self.spo_numbers
            or self.supplier_codes
            or self.promotion_ids
            or self.attachment_filenames
            or self.form_codes
        )

    def as_echo(self) -> dict[str, Any]:
        """Shape for `_resolved_entities` in tool responses."""
        return {
            "resolved": self.resolved,
            "ambiguous": self.ambiguous,
            "unresolved": self.unresolved,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


def _dedupe_preserve_order(values: Iterable[Optional[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if not v:
            continue
        s = str(v)
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


RAG_MIN_SIMILARITY = 0.20
RAG_TOP_K = 15
RAG_AMBIGUOUS_GAP = 0.04


def _rag_resolve_phrase(
    db: Session,
    phrase: str,
    allowed_entity_types: frozenset[str],
    *,
    top_k: int = RAG_TOP_K,
    min_similarity: float = RAG_MIN_SIMILARITY,
) -> list[ResolvedEntity]:
    """One embed call + top-k vector search against embedding_chunks.

    Returns the top-k hits filtered by `allowed_entity_types`, ranked by cosine
    similarity desc, dropping anything below `min_similarity`. No tokenization,
    no fallback tiers - the entire surface area is "embed the user's phrase,
    ask pgvector who looks closest, trust the score."

    `embedding_documents.source_key` is populated per source_type by the embedding
    backfill service (`product` → product_code, `transporter` → code,
    `customer_order` → order_number, `customer` → customer_code or
    `debtor:<code>`), so the returned `canonical_code` is the value the caller
    should feed directly into SQL filter buckets.
    """
    try:
        from app.services.embedding_worker import _embed_text_chunks
    except Exception:
        logger.exception("RAG embedding worker import failed; phrase=%s", phrase)
        return []

    allowed_source_types = [
        src for src, ent in _EMBEDDING_SOURCE_TYPES.items() if ent in allowed_entity_types
    ]
    if not allowed_source_types:
        return []

    try:
        query_vec = _embed_text_chunks([phrase])[0]
    except Exception:
        logger.exception("RAG embed failed for phrase=%s", phrase)
        return []

    sql = text(
        """
        SELECT ec.source_type, ec.source_id, ed.source_key,
               1 - (ec.embedding <=> CAST(:vec AS vector)) AS similarity
        FROM embedding_chunks ec
        JOIN embedding_documents ed
          ON ed.source_type = ec.source_type AND ed.source_id = ec.source_id
        WHERE ec.is_current = TRUE
          AND ec.source_type = ANY(:types)
        ORDER BY ec.embedding <=> CAST(:vec AS vector)
        LIMIT :k
        """
    )
    try:
        # SAVEPOINT: this lookup is best-effort, and a failed statement
        # otherwise leaves the whole transaction aborted - every later
        # query in the request would die on InFailedSqlTransaction.
        with db.begin_nested():
            rows = db.execute(
                sql,
                {"vec": list(query_vec), "types": allowed_source_types, "k": top_k},
            ).all()
    except Exception:
        logger.exception("RAG vector query failed for phrase=%s", phrase)
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback after RAG query failure also failed")
        return []

    out: list[ResolvedEntity] = []
    seen_keys: set[tuple[str, str]] = set()
    for r in rows:
        sim = float(r.similarity or 0.0)
        if sim < min_similarity:
            continue
        entity_type = _EMBEDDING_SOURCE_TYPES.get(str(r.source_type))
        if not entity_type:
            continue
        raw_key = r.source_key or str(r.source_id)
        # Customer debtor-synthetic keys arrive as "debtor:<code>" - strip the
        # prefix so the canonical_code matches `Order.debtor_code` directly.
        match_field = "embedding"
        canonical = raw_key
        if entity_type == "customer" and isinstance(raw_key, str) and raw_key.startswith("debtor:"):
            canonical = raw_key.split(":", 1)[1]
            match_field = "debtor_code"
        elif entity_type == "customer":
            match_field = "customer_code"
        elif entity_type == "product":
            match_field = "product_code"
        elif entity_type == "transporter":
            match_field = "transporter_code"
        elif entity_type == "customer_order":
            match_field = "order_number"
        elif entity_type == "inbound_shipment":
            match_field = "shipment_number"
        elif entity_type == "spo_allocation":
            match_field = "spo_number"
        elif entity_type == "grn":
            match_field = "picking_number"
        elif entity_type == "promotion":
            match_field = "promotion_id"
        elif entity_type == "attachment":
            match_field = "filename"
        elif entity_type == "form":
            match_field = "form_code"
        dedupe_key = (entity_type, canonical.lower() if canonical else str(r.source_id))
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        out.append(
            ResolvedEntity(
                entity_type=entity_type,
                canonical_code=canonical,
                match_field=match_field,
                match_tier="embedding",
                similarity=sim,
                display={"source_type": str(r.source_type)},
            )
        )

    # Substring re-rank: embedding similarity on short product/customer codes is
    # noisy - the top-K above similarity threshold is often a mix of relevant
    # and completely unrelated codes that happen to share token shape. When ANY
    # candidate's canonical_code (case-insensitive) contains the input phrase
    # as a substring, keep only those candidates - substring containment is a
    # stronger human-meaningful signal than embedding rank, and it cuts the
    # noise from 15 candidates down to the ones the user could plausibly mean.
    phrase_lower = (phrase or "").strip().lower()
    if phrase_lower and len(phrase_lower) >= 3:
        substring_hits = [
            h for h in out
            if h.canonical_code and phrase_lower in str(h.canonical_code).lower()
        ]
        if substring_hits:
            return substring_hits
    return out


def _drop_hidden_shipments(db: Session, aggregated: list, regions) -> None:
    """Remove every inbound_shipment match (and alternative) this contact's `regions` do not
    serve. Keyed on the shipment uuid; a match with no uuid is dropped (fail closed)."""
    ids = {
        str(m.uuid)
        for tr in aggregated
        for m in list(tr.matches) + list(getattr(tr, "alternatives", None) or [])
        if m.entity_type == "inbound_shipment" and m.uuid
    }
    visible = set()
    if ids:
        visible = {
            str(i)
            for (i,) in db.query(InboundShipment.id)
            .filter(InboundShipment.id.in_(ids), _region_overlap_clause(regions))
            .all()
        }

    def _keep(m) -> bool:
        return m.entity_type != "inbound_shipment" or (m.uuid is not None and str(m.uuid) in visible)

    for tr in aggregated:
        tr.matches = [m for m in tr.matches if _keep(m)]
        if getattr(tr, "alternatives", None):
            tr.alternatives = [m for m in tr.alternatives if _keep(m)]
        if len(tr.matches) < 2:
            tr.ambiguous = False


def resolve_entities_to_filters(
    db: Session,
    entities: Optional[list[str]],
    *,
    allowed_entity_types: Iterable[str],
    max_candidates: int = 8,
    regions=None,
) -> EntityFilterBuckets:
    """Resolve a free-text `entities` bag via pure-RAG top-k vector search.

    Strategy: for each entity string, one embedding call + one pgvector top-K
    lookup against `embedding_chunks` restricted to source_types matching
    `allowed_entity_types`. The top hit is treated as the resolved entity; if
    the top-2 hits are within `RAG_AMBIGUOUS_GAP` of each other the input is
    flagged ambiguous and ALL near-tie candidates surface in the echo so the
    agent can ask the user to pick one. Anything below `RAG_MIN_SIMILARITY`
    falls into `unresolved` so the agent can tell the user no match was found.

    No token extraction, no n-gram sweep, no Tier-1 / Tier-2 ILIKE - the
    embedding pipeline already indexed the relevant business vocabulary, so we
    let pgvector do the work in one round-trip per entity.
    """
    buckets = EntityFilterBuckets()
    if not entities:
        return buckets

    allowed_set = frozenset(allowed_entity_types)
    inputs = [s.strip() for s in entities if s and s.strip()]
    if not inputs:
        return buckets

    t_start = time.perf_counter()
    aggregated: list[TokenResolution] = []
    for phrase in inputs:
        # Hybrid retrieval. The agent's input is unpredictable - sometimes a full
        # product_code, sometimes a partial prefix the user typed, sometimes a
        # loose customer phrase. Sequence:
        #   1. Tier-2 prefix / substring ILIKE across every allowed entity type's
        #      name + code columns. Deterministic. Matches what the in-app list
        #      search does - "SRTWC8088" returns every product_code containing it.
        #   2. Only if Tier-2 finds nothing → RAG embedding top-K fallback.
        #      Catches semantic phrasings ("fira ventures" → "FIRA VENTURE
        #      ENTERPRISE SDN BHD") that pure substring would miss.
        substring_hits = _tier2_fuzzy_lookup(
            db, phrase, allowed_entity_types=allowed_set, regions=regions
        )
        if substring_hits:
            ambiguous = len(substring_hits) > 1
            aggregated.append(
                TokenResolution(
                    token=phrase,
                    matches=substring_hits,
                    ambiguous=ambiguous,
                )
            )
            continue

        # Tier 2.5: pg_trgm fuzzy SQL - catches typos / missing chars before
        # falling through to the heavier embedding lookup.
        trgm_hits = _trgm_lookup(db, phrase, allowed_set)
        if trgm_hits:
            aggregated.append(
                TokenResolution(
                    token=phrase,
                    matches=trgm_hits,
                    ambiguous=len(trgm_hits) > 1,
                )
            )
            continue

        hits = _rag_resolve_phrase(
            db,
            phrase,
            allowed_set,
            top_k=max(RAG_TOP_K, 1),
            min_similarity=RAG_MIN_SIMILARITY,
        )
        if not hits:
            aggregated.append(TokenResolution(token=phrase, matches=[]))
            continue
        top = hits[0]
        same_type_candidates = [h for h in hits if h.entity_type == top.entity_type]
        if len(same_type_candidates) > 1:
            aggregated.append(
                TokenResolution(
                    token=phrase,
                    matches=same_type_candidates,
                    ambiguous=True,
                )
            )
        else:
            aggregated.append(TokenResolution(token=phrase, matches=[top]))
    # Same isolation boundary as the resolve endpoint: the trigram and embedding
    # tiers above are raw SQL, so without this a list tool would filter stock /
    # orders by another company's canonical codes.
    _apply_company_scope(db, aggregated)
    if regions is not None:
        _drop_hidden_shipments(db, aggregated, regions)

    result = ResolutionResult(
        tokens=[tr.token for tr in aggregated],
        resolutions=aggregated,
        elapsed_ms=(time.perf_counter() - t_start) * 1000.0,
    )
    buckets.elapsed_ms = result.elapsed_ms

    for tr in result.resolutions:
        if tr.ambiguous:
            # Surface the ambiguity in the echo so the agent can tell the user
            # we matched several near-equal candidates, but ALSO let every
            # candidate flow into the filter buckets below. Returning empty on
            # ambiguity is worse than over-matching - caller sees stock for all
            # near matches and the user can disambiguate verbally.
            buckets.ambiguous.append(
                {
                    "input": tr.token,
                    "candidates": [
                        {
                            "type": m.entity_type,
                            "canonical_code": m.canonical_code,
                            "match_field": m.match_field,
                            "match_tier": m.match_tier,
                            "display": m.display,
                        }
                        for m in tr.matches
                    ],
                }
            )
        if not tr.matches:
            buckets.unresolved.append(tr.token)
            continue
        for m in tr.matches:
            if m.entity_type not in allowed_set:
                # Resolver may emit related types we don't filter on; skip silently.
                continue
            if m.entity_type == "product":
                buckets.product_codes.append(m.canonical_code)
            elif m.entity_type == "customer_order":
                buckets.order_numbers.append(m.canonical_code)
            elif m.entity_type == "customer":
                if m.match_field == "debtor_name":
                    buckets.debtor_names.append(m.canonical_code)
                elif m.match_field in ("customer_code", "debtor_code"):
                    buckets.customer_codes.append(m.canonical_code)
                else:
                    buckets.customer_names.append(m.canonical_code)
            elif m.entity_type == "transporter":
                if m.match_field == "transporter_code":
                    buckets.transporter_codes.append(m.canonical_code)
                else:
                    buckets.transporter_names.append(
                        m.display.get("name") or m.canonical_code
                    )
                    if m.canonical_code:
                        buckets.transporter_codes.append(m.canonical_code)
            elif m.entity_type == "inbound_shipment":
                buckets.shipment_numbers.append(m.canonical_code)
            elif m.entity_type == "grn":
                buckets.picking_numbers.append(m.canonical_code)
            elif m.entity_type == "spo_allocation":
                buckets.spo_numbers.append(m.canonical_code)
            elif m.entity_type == "supplier":
                buckets.supplier_codes.append(m.canonical_code)
            elif m.entity_type == "promotion":
                buckets.promotion_ids.append(m.canonical_code)
            elif m.entity_type == "attachment":
                buckets.attachment_filenames.append(m.canonical_code)
            elif m.entity_type == "form":
                buckets.form_codes.append(m.canonical_code)
            buckets.resolved.append(
                {
                    "input": tr.token,
                    "type": m.entity_type,
                    "canonical_code": m.canonical_code,
                    "match_field": m.match_field,
                    "match_tier": m.match_tier,
                    "similarity": m.similarity,
                    "display": m.display,
                }
            )

    buckets.product_codes = _dedupe_preserve_order(buckets.product_codes)
    buckets.debtor_names = _dedupe_preserve_order(buckets.debtor_names)
    buckets.customer_codes = _dedupe_preserve_order(buckets.customer_codes)
    buckets.customer_names = _dedupe_preserve_order(buckets.customer_names)
    buckets.transporter_codes = _dedupe_preserve_order(buckets.transporter_codes)
    buckets.transporter_names = _dedupe_preserve_order(buckets.transporter_names)
    buckets.order_numbers = _dedupe_preserve_order(buckets.order_numbers)
    buckets.shipment_numbers = _dedupe_preserve_order(buckets.shipment_numbers)
    buckets.picking_numbers = _dedupe_preserve_order(buckets.picking_numbers)
    buckets.spo_numbers = _dedupe_preserve_order(buckets.spo_numbers)
    buckets.supplier_codes = _dedupe_preserve_order(buckets.supplier_codes)
    buckets.promotion_ids = _dedupe_preserve_order(buckets.promotion_ids)
    buckets.attachment_filenames = _dedupe_preserve_order(buckets.attachment_filenames)
    buckets.form_codes = _dedupe_preserve_order(buckets.form_codes)
    return buckets
