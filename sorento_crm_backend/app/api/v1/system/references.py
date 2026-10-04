"""Entity reference resolution endpoint.

Exposes the deterministic entity resolver as an HTTP API so the MCP layer (or any
external caller) can disambiguate codes mid-turn. The resolver itself lives in
`app.services.entity_resolver`.
"""
import logging
import re
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import String as _String
from sqlalchemy import cast as _cast
from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import ARRAY as _ARRAY
from sqlalchemy.orm import Session

from sqlalchemy import or_ as _sa_or

from app.database import get_db
from app.dependencies import get_external_api_user
from app.models.access import ContactAccessType
from app.models.company import Company
from app.models.marketing import Promotion, PromotionProduct
from app.services import promotion_serving, promotion_window
from app.models.product import Brand, Product
from app.models.resources import Attachment, AttachmentType
from app.services.entity_resolver import (
    _CODE_RE,
    EntityPinMismatch,
    _canonical_entity_type,
    _with_resolve_memo,
    fetch_product_brands,
    resolve_references,
    resolve_references_intersection,
    token_word_coverage_for_rows,
)
from app.services.error_handler import AppException


# Canonical entity types the resolver understands. domain_hint matching one of
# these is treated as an entity-scope hint (merged into allowed_entity_types)
# rather than an AttachmentType lookup. Includes the brand / category fan-out
# aliases so callers can pass either the alias or the concrete type.
_RESOLVER_ENTITY_TYPES: frozenset[str] = frozenset({
    "product",
    "product_set",
    "customer_order",
    "customer",
    "inbound_shipment",
    "spo_allocation",
    "grn",
    "warehouse",
    "supplier",
    "promotion",
    "transporter",
    "form",
    "attachment",
    "attachment_type",
    "certificate",
    "brand",
    "category",
})
from app.services.query_normalizer import DOMAIN_STOPWORDS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/references")


_ALLOWED_MATCH_MODES = {"or", "and"}

# Union of every domain's noise words. Tokens like "order 202605-2651" come in
# from chatbot phrasing - the user means the order_number, not a literal
# substring. Cleaning leaves "202605-2651" so the exact-match probe succeeds.
_ENTITY_STOPWORDS: frozenset[str] = frozenset().union(*DOMAIN_STOPWORDS.values())
_TOKEN_PUNCT_STRIP = ".,!?;:\"'()[]{}"


def _resolve_attachment_type_for_hint(db: Session, hint: str):
    """Resolve a domain_hint string → AttachmentType row, or None.

    Mirrors the matcher in `AttachmentService.list_attachments(attachment_type_code=...)`
    so MCP / API callers get identical type-resolution behavior whether they
    pre-filter attachments or use this resolver.
    """
    if not hint or not hint.strip():
        return None
    code_norm = hint.strip()
    variants = {code_norm}
    low = code_norm.lower()
    if low == "catalog":
        variants.add("catalogue")
    elif low == "catalogue":
        variants.add("catalog")
    for variant in variants:
        row = (
            db.query(AttachmentType)
            .filter(AttachmentType.code.ilike(variant))
            .first()
            or db.query(AttachmentType)
            .filter(AttachmentType.type_name.ilike(variant))
            .first()
        )
        if row is not None:
            return row
    return None


def _resolve_with_domain_hint(
    db: Session,
    hint: str,
    tokens: list[str],
    query_text: str,
) -> dict[str, Any]:
    """Short-circuit resolver: ILIKE search within one AttachmentType bucket.

    Returns a ResolutionResult-shaped dict. Per-token search on filename +
    description, scoped to `attachment_type_id = <resolved hint type>`. When
    the hint resolves to no AttachmentType, returns an empty payload with
    `domain_hint_unresolved=True` so the agent knows the hint label was bad.

    Every match carries `company_id` / `company_name`, the same attribution the
    main resolver stamps in `_attach_company_info` - this short-circuit is the
    path a document request actually takes, and a contact granted two companies
    gets one current workbook from each.
    """
    type_row = _resolve_attachment_type_for_hint(db, hint)
    base_empty: dict[str, Any] = {
        "tokens": list(tokens) if tokens else ([query_text] if query_text else []),
        "elapsed_ms": 0.0,
        "resolutions": [],
        "unresolved_tokens": [],
        "ambiguous_tokens": [],
        "domain_hint": hint,
    }
    if type_row is None:
        base_empty["domain_hint_unresolved"] = True
        return base_empty

    # Build search term list. Prefer explicit tokens; fall back to splitting
    # the free-text query on whitespace (≥3 chars per term).
    if tokens:
        terms = [t.strip() for t in tokens if t and t.strip()]
    else:
        terms = [w for w in (query_text or "").split() if len(w) >= 3]
    if not terms:
        # Empty input - return all attachments of this type capped at 20 so the
        # caller still gets the catalogue browse shape.
        terms = [""]

    type_id = str(type_row.id)
    PER_TOKEN_CAP = 20
    base_empty["domain_hint_attachment_type_id"] = type_id
    base_empty["domain_hint_attachment_type_name"] = type_row.type_name

    resolutions: list[dict[str, Any]] = []
    for term in terms:
        # LEFT JOIN, not a filter: company ISOLATION is already done by the
        # `do_orm_execute` scope filter on this ORM query. This only ATTRIBUTES
        # the rows that survive it, so a contact granted both Mocha and Sorento
        # gets both current workbooks and can tell them apart - two files named
        # "Container Status 2026.xlsx" are otherwise indistinguishable.
        q = (
            db.query(
                Attachment.id,
                Attachment.original_filename,
                Attachment.description,
                Attachment.mime_type,
                Attachment.full_directory_path,
                Attachment.company_id,
                Company.name,
            )
            .outerjoin(Company, Company.id == Attachment.company_id)
            .filter(
                Attachment.attachment_type_id == type_id,
                Attachment.is_deleted.is_(False),
            )
        )
        if term:
            wild = f"%{term}%"
            q = q.filter(
                _sa_or(
                    Attachment.original_filename.ilike(wild),
                    Attachment.description.ilike(wild),
                )
            )
        rows = q.limit(PER_TOKEN_CAP).all()
        matches = [
            {
                "entity_type": "attachment",
                "canonical_code": filename,
                "uuid": str(aid) if aid else None,
                "match_field": "original_filename",
                "match_tier": "substring" if term else "scope",
                "similarity": None,
                # Same shape the main resolver emits (`_attach_company_info`):
                # at the match level for callers that read the match, and again
                # inside `display` for renderers that only read `display`.
                "company_id": str(company_id) if company_id else None,
                "company_name": company_name,
                "display": {
                    "filename": filename,
                    "description": description,
                    "attachment_type": type_row.type_name,
                    "mime_type": mime,
                    "directory": dir_path,
                    "company_id": str(company_id) if company_id else None,
                    "company_name": company_name,
                },
            }
            for aid, filename, description, mime, dir_path, company_id, company_name in rows
        ]
        resolutions.append(
            {
                "token": term or hint,
                "resolved": len(matches) == 1,
                "ambiguous": len(matches) > 1,
                "matches": matches,
            }
        )
    base_empty["resolutions"] = resolutions
    base_empty["unresolved_tokens"] = [r["token"] for r in resolutions if not r["matches"]]
    base_empty["ambiguous_tokens"] = [r["token"] for r in resolutions if r["ambiguous"]]
    return base_empty


def _strip_entity_stopwords(token: str) -> str:
    """Drop entity-type noise words from a multi-word token; preserve original on empty result."""
    if not token:
        return token
    raw = token.strip()
    if " " not in raw:
        return token
    parts = [p for p in raw.split() if p]
    kept = [p for p in parts if p.lower().strip(_TOKEN_PUNCT_STRIP) not in _ENTITY_STOPWORDS]
    cleaned = " ".join(kept).strip()
    return cleaned or token


# Entity types whose canonical labels are pure-alpha words ("catalogue",
# "brochure", "spec sheet") and therefore bypass the code-token regex used by
# `extract_candidate_tokens`. When the caller hints one of these types in
# `allowed_entity_types` but supplies no explicit `tokens`, we synthesize tokens
# by splitting the query on whitespace so the probes still receive candidates.
_ALPHA_TOKEN_TYPES: frozenset[str] = frozenset({"attachment_type"})


def _synthesize_alpha_tokens(query: str) -> list[str]:
    """Whitespace split → drop stopwords / punctuation / short noise.

    Mirrors `extract_candidate_tokens` semantics for pure-alpha entity types.
    Keeps each word as its own token (single-word lookup), plus the full
    cleaned phrase (multi-word lookup like "spec sheet") so both shapes match.
    """
    if not query or not query.strip():
        return []
    words = [
        w.strip(_TOKEN_PUNCT_STRIP)
        for w in query.split()
        if w.strip(_TOKEN_PUNCT_STRIP)
    ]
    out: list[str] = []
    seen: set[str] = set()
    cleaned_words = [w for w in words if len(w) >= 3 and w.lower() not in _ENTITY_STOPWORDS]
    if len(cleaned_words) >= 2:
        phrase = " ".join(cleaned_words).strip()
        if phrase:
            out.append(phrase)
            seen.add(phrase.lower())
    for w in cleaned_words:
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


_ACCESS_FILTERED_TYPES: frozenset[str] = frozenset({"promotion", "attachment"})


def _apply_limit(result: dict[str, Any], limit: int | None) -> dict[str, Any]:
    """Cap per-token `matches` and `intersection` to `limit`. None / <=0 = no cap.

    Re-derives `resolved` and `unresolved_tokens` after truncation so callers
    don't see stale flags. `by_entity_type` regrouped from the truncated
    intersection so AND-mode counts stay consistent.
    """
    if not isinstance(result, dict) or not limit or limit <= 0:
        return result
    new_result = dict(result)
    if isinstance(result.get("resolutions"), list):
        new_resolutions = []
        for tr in result["resolutions"]:
            new_tr = dict(tr)
            matches = list(tr.get("matches") or [])
            if len(matches) > limit:
                matches = matches[:limit]
            new_tr["matches"] = matches
            new_tr["resolved"] = len(matches) == 1 and not tr.get("ambiguous", False)
            new_resolutions.append(new_tr)
        new_result["resolutions"] = new_resolutions
        new_result["unresolved_tokens"] = [
            r["token"] for r in new_resolutions if not (r.get("matches") or [])
        ]
    if isinstance(result.get("intersection"), list):
        capped = list(result["intersection"])[:limit]
        new_result["intersection"] = capped
        new_result["empty"] = not capped
        by_type: dict[str, list[dict[str, Any]]] = {}
        for m in capped:
            by_type.setdefault(m.get("entity_type", ""), []).append(m)
        new_result["by_entity_type"] = by_type
    return new_result


def _apply_limit_marking_truncation(
    result: dict[str, Any], limit: int | None
) -> dict[str, Any]:
    """`_apply_limit`, plus: any entity type it cut rows from joins
    `_truncated_entity_types`, so the coverage claim over that type is flagged
    incomplete rather than asserted as if the caller saw everything."""
    if not isinstance(result, dict) or not isinstance(result.get("intersection"), list):
        return _apply_limit(result, limit)
    pre: dict[str, int] = {}
    for m in result["intersection"]:
        et = str((m or {}).get("entity_type") or "")
        pre[et] = pre.get(et, 0) + 1
    capped = _apply_limit(result, limit)
    post: dict[str, int] = {}
    for m in capped.get("intersection") or []:
        et = str((m or {}).get("entity_type") or "")
        post[et] = post.get(et, 0) + 1
    cut = {t for t, n in pre.items() if post.get(t, 0) < n}
    if cut:
        capped["_truncated_entity_types"] = sorted(
            set(capped.get("_truncated_entity_types") or []) | cut
        )
    return capped


def _attach_and_coverage(result: dict[str, Any]) -> dict[str, Any]:
    """Final step for every AND-shaped result: compute `token_coverage` over
    the rows ACTUALLY being returned, then strip the transport keys.

    Runs after `_apply_promotion_access_levels_filter`,
    `_expand_products_via_promotions` and `_apply_limit` have all had their
    turn - coverage computed any earlier describes rows a later stage removed
    (the original version asserted "every word matched" on zero-row
    entitlement-filtered responses). No-op for OR-shaped results.

    Best-effort by design: the rows are the answer, the coverage is commentary
    on them, so a coverage failure must never 500 a resolve that succeeded.
    The transport-key strip is unconditional either way.
    """
    if not isinstance(result, dict) or "intersection" not in result:
        return result
    try:
        truncated = frozenset(result.get("_truncated_entity_types") or [])
        result["token_coverage"] = token_word_coverage_for_rows(
            result.get("tokens") or [],
            result.get("intersection") or [],
            truncated_types=truncated,
        )
    except Exception:
        logger.exception("token_coverage computation failed; omitting the field")
    finally:
        result.pop("_truncated_entity_types", None)
        # The filters rebuild `by_entity_type` from the same row dicts as
        # `intersection`, so the blob can appear in both views - strip both.
        for m in result.get("intersection") or []:
            if isinstance(m, dict):
                m.pop("_match_blob", None)
        for rows in (result.get("by_entity_type") or {}).values():
            if isinstance(rows, list):
                for m in rows:
                    if isinstance(m, dict):
                        m.pop("_match_blob", None)
    return result


def _stamp_brand_on_products(db: Session, result: dict[str, Any]) -> dict[str, Any]:
    """Fill `display.brand` on any product row in the payload that lacks it.

    The resolver stamps every match it builds itself (`_attach_brand_info`), but
    this module builds product rows of its own - the through-promotion expansion
    and the spec search - as plain dicts that never pass through it. Rather than
    patch each builder (and every future one), sweep the finished payload: a row
    that already carries a brand is left exactly as it is, so this is idempotent
    and the brand-access path keeps the shape it always had.

    Mutates in place: `by_entity_type` shares its `display` dicts with
    `intersection`, so stamping once updates both views of the same row.
    """
    if not isinstance(result, dict):
        return result

    pending: list[dict[str, Any]] = []

    def _collect(rows: Any) -> None:
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict) or row.get("entity_type") != "product":
                continue
            display = row.get("display")
            if not isinstance(display, dict) or "brand" in display or not row.get("uuid"):
                continue
            pending.append(row)

    for tr in result.get("resolutions") or []:
        if isinstance(tr, dict):
            _collect(tr.get("matches"))
            _collect(tr.get("alternatives"))
    _collect(result.get("intersection"))
    _collect(result.get("alternatives"))
    for rows in (result.get("by_entity_type") or {}).values():
        _collect(rows)

    if not pending:
        return result
    try:
        brands = fetch_product_brands(db, [str(r["uuid"]) for r in pending])
    except Exception:  # noqa: BLE001 - brand is additive, never fatal
        logger.exception("brand stamp on resolve payload failed")
        return result
    for row in pending:
        key = str(row["uuid"])
        if key in brands:
            row["display"]["brand"] = brands[key]
    return result


def _resolve_promotion_ids_for_token(
    db: Session, token: str, allowed_access_codes: set[str] | None
) -> set[str]:
    """Find promotion UUIDs whose description matches `token`.

    Contiguous substring + per-word AND search on Promotion.description.
    When `allowed_access_codes` is non-empty, intersects with
    Promotion.access_levels JSONB so the agent only ever surfaces promos the
    contact can see.
    """
    tok = (token or "").strip()
    if not tok:
        return set()
    q = db.query(Promotion.id, Promotion.description, Promotion.access_levels)
    q = q.filter(Promotion.description.ilike(f"%{tok}%"))
    rows = q.limit(50).all()
    # Per-word AND fallback for multi-word phrases ("water closet promo").
    if not rows:
        words = [w for w in tok.split() if len(w) >= 3]
        if words:
            q2 = db.query(Promotion.id, Promotion.description, Promotion.access_levels)
            for w in words:
                q2 = q2.filter(Promotion.description.ilike(f"%{w}%"))
            rows = q2.limit(50).all()
    out: set[str] = set()
    for pid, _desc, levels in rows:
        if allowed_access_codes:
            if not isinstance(levels, list) or not allowed_access_codes.intersection(levels):
                continue
        out.add(str(pid))
    return out


def _apply_serving_policy_to_promo_matches(
    db: Session, matches: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Drop promotion matches the serving policy withholds, and stamp the rest.

    The resolver's own promotion probe finds rows by description text, which is
    a different door into the same answer - so it gets the same policy as the
    product walk and the name probe. Matches without a resolvable UUID are left
    alone rather than silently dropped.
    """
    if not matches:
        return matches
    ids = [str(m.get("uuid")) for m in matches if m.get("uuid")]
    if not ids:
        return matches

    today = _today()
    verdict = promotion_serving.evaluate_candidates(db, ids, today)
    kept: list[dict[str, Any]] = []
    for match in matches:
        key = str(match.get("uuid")) if match.get("uuid") else None
        if key is None:
            kept.append(match)
            continue
        if not verdict.is_served(key):
            continue
        promo_type = verdict.type_by_promotion.get(key)
        display = match.setdefault("display", {})
        display["promotion_type_code"] = getattr(promo_type, "type_code", None)
        display["promotion_type_name"] = getattr(promo_type, "type_name", None)
        display["expired_but_usable"] = verdict.is_expired_but_usable(key)
        kept.append(match)
    return kept


def _build_promotion_resolutions(
    db: Session, promotion_ids: set[str]
) -> list[dict[str, Any]]:
    """Synthesize promotion ResolvedEntity dicts for the supplied promo UUIDs.

    Used when `domain_hint=promotion` and the resolver itself didn't run the
    promotion probe (e.g. caller's whitelist was `[product]`). Returns
    `entity_type=promotion` rows so the agent sees promotion candidates ranked
    first - domain_hint is highest priority.
    """
    if not promotion_ids:
        return []
    rows = (
        db.query(
            Promotion.id,
            Promotion.description,
            Promotion.is_active,
            Promotion.start_date,
            Promotion.end_date,
        )
        .filter(Promotion.id.in_(promotion_ids))
        .all()
    )
    if not rows:
        return []

    # The same per-type serving policy the product walk applies. Naming a promo
    # is not a licence to be told about one that cannot be honoured: without
    # this, asking for "special promo" by name returned an expired special that
    # the product route would have withheld, so one endpoint answered the same
    # question two ways.
    today = _today()
    verdict = promotion_serving.evaluate_candidates(db, [str(r[0]) for r in rows], today)

    resolutions: list[dict[str, Any]] = []
    for pid, desc, is_active, start_date, end_date in rows:
        key = str(pid)
        if not verdict.is_served(key):
            continue
        promo_type = verdict.type_by_promotion.get(key)
        live = promotion_window.is_live(is_active, start_date, end_date, today)
        resolutions.append(
            {
                "entity_type": "promotion",
                "canonical_code": key,
                "uuid": key,
                "match_field": "description",
                "match_tier": "domain_hint",
                "similarity": None,
                "display": {
                    "description": desc,
                    "is_active": live,
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                    "promotion_type_code": getattr(promo_type, "type_code", None),
                    "promotion_type_name": getattr(promo_type, "type_name", None),
                    "is_expired": not live,
                    "expired_but_usable": verdict.is_expired_but_usable(key),
                },
            }
        )
    return resolutions


def _build_product_resolutions_from_promotions(
    db: Session, token: str, promotion_ids: set[str]
) -> list[dict[str, Any]]:
    """For each product in the given promotions, build a ResolvedEntity dict.

    Tagged `entity_type=product`, `match_tier=through_promotion`,
    `match_field=promotion_membership`. Display carries the promotion
    description(s) the product is linked to so the agent can answer "X is in
    these promos" without an extra round-trip.
    """
    if not promotion_ids:
        return []
    rows = (
        db.query(
            Product.id,
            Product.product_code,
            Product.product_name,
            PromotionProduct.promotion_id,
            Promotion.description,
        )
        .join(PromotionProduct, PromotionProduct.product_id == Product.id)
        .join(Promotion, Promotion.id == PromotionProduct.promotion_id)
        .filter(PromotionProduct.promotion_id.in_(promotion_ids))
        .all()
    )
    by_product: dict[str, dict[str, Any]] = {}
    for pid, code, name, promo_id, promo_desc in rows:
        key = str(pid)
        entry = by_product.setdefault(
            key,
            {
                "entity_type": "product",
                "canonical_code": code,
                "uuid": key,
                "match_field": "promotion_membership",
                "match_tier": "through_promotion",
                "similarity": None,
                "display": {
                    "product_name": name,
                    "via_token": token,
                    "promotions": [],
                },
            },
        )
        promos = entry["display"]["promotions"]
        if {"promotion_id": str(promo_id), "description": promo_desc} not in promos:
            promos.append({"promotion_id": str(promo_id), "description": promo_desc})
    return list(by_product.values())


def _build_promotions_for_products(
    db: Session, product_uuids: set[str], allowed_access_codes: set[str] | None
) -> list[dict[str, Any]]:
    """Reverse membership walk: promotions that CONTAIN the given product UUIDs.

    The description-text probe (`_resolve_promotion_ids_for_token`) can't find a
    promo from a product SKU - a SKU never appears in the promo description. When
    the token already resolved to product(s), this walks `promotion_products`
    backward so `domain_hint=promotion` can still answer "the promo for X".

    Surfaces expired promos too when their TYPE still honours them, flagged
    `display.expired_but_usable`, so the answer can read "found, ended on 31/07,
    still applies" instead of a blank. The per-type serving policy
    (`app/services/promotion_serving.py`) decides, so this resolver and the
    promotions list cannot disagree about which promotion answers the question:
    a live promotion always wins, a type with no live promotion may contribute
    its latest expired one, and an expired `special` is never returned.

    When `allowed_access_codes` is non-empty, filters by
    `Promotion.access_levels` intersection (mirrors the description probe's
    gating) BEFORE the policy runs, so a contact's own candidate set is what gets
    ranked.
    """
    if not product_uuids:
        return []
    rows = (
        db.query(
            Promotion.id,
            Promotion.description,
            Promotion.is_active,
            Promotion.access_levels,
            Promotion.start_date,
            Promotion.end_date,
            Product.product_code,
        )
        .join(PromotionProduct, PromotionProduct.promotion_id == Promotion.id)
        .join(Product, Product.id == PromotionProduct.product_id)
        .filter(PromotionProduct.product_id.in_(product_uuids))
        .all()
    )
    by_promo: dict[str, dict[str, Any]] = {}
    for pid, desc, is_active, levels, start_date, end_date, code in rows:
        if allowed_access_codes:
            if not isinstance(levels, list) or not allowed_access_codes.intersection(levels):
                continue
        key = str(pid)
        entry = by_promo.setdefault(
            key,
            {
                "entity_type": "promotion",
                "canonical_code": key,
                "uuid": key,
                "match_field": "promotion_membership",
                "match_tier": "via_product",
                "similarity": None,
                "display": {
                    "description": desc,
                    # The LIVE definition, not the raw column: the daily sync job
                    # papers over a window that lapsed today, and until it ticks
                    # the flag says active for a promotion that ended yesterday.
                    "is_active": promotion_window.is_live(
                        is_active, start_date, end_date, _today()
                    ),
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                    "products": [],
                },
            },
        )
        if code not in entry["display"]["products"]:
            entry["display"]["products"].append(code)

    if not by_promo:
        return []

    today = _today()
    verdict = promotion_serving.evaluate_candidates(db, list(by_promo.keys()), today)
    served: list[dict[str, Any]] = []
    for key, entry in by_promo.items():
        if not verdict.is_served(key):
            continue
        promo_type = verdict.type_by_promotion.get(key)
        entry["display"]["promotion_type_code"] = getattr(promo_type, "type_code", None)
        entry["display"]["promotion_type_name"] = getattr(promo_type, "type_name", None)
        entry["display"]["is_expired"] = not entry["display"]["is_active"]
        entry["display"]["expired_but_usable"] = verdict.is_expired_but_usable(key)
        served.append(entry)
    return served


def _today() -> date:
    return datetime.utcnow().date()


def _translate_access_names_to_codes(
    db: Session, names: list[str] | None
) -> set[str]:
    """Lowercase-name lookup against contact_access_types.code. Empty in → empty out."""
    if not names:
        return set()
    lowered = {n.strip().lower() for n in names if n and n.strip()}
    if not lowered:
        return set()
    return {
        r[0]
        for r in db.query(ContactAccessType.code)
        .filter(func.lower(ContactAccessType.name).in_(lowered))
        .all()
    }


def _resolve_products_by_brand_access(
    db: Session,
    token: str,
    access_codes: set[str],
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Find products whose brand.access_levels intersect `access_codes` AND
    whose code / name / description matches `token` (substring + per-word AND).

    Used by the promotion-domain fallback: when no promotion matches the
    token under the caller's access levels, surface products from brands the
    contact can see - gives the agent enough context to answer "no promo
    found, but here are matching products".

    `access_codes` is REQUIRED for this helper to run. Callers must scope to
    the promotion-domain path; other domains should use the regular product
    probes (no brand-access gating).
    """
    if not token or not access_codes:
        return []
    tok = token.strip()
    base = (
        db.query(
            Product.id,
            Product.product_code,
            Product.product_name,
            Brand.id.label("brand_id"),
            Brand.brand_code,
            Brand.brand_name,
        )
        .join(Brand, Brand.id == Product.brand_id)
        .filter(
            Brand.access_levels.op("?|")(
                _cast(list(access_codes), _ARRAY(_String))
            )
        )
    )
    # Contiguous substring on code/name/description.
    sub = f"%{tok}%"
    rows = (
        base.filter(
            or_(
                Product.product_code.ilike(sub),
                Product.product_name.ilike(sub),
                Product.description.ilike(sub),
            )
        )
        .limit(limit)
        .all()
    )
    # Per-word AND fallback for multi-word phrases that don't match
    # contiguously (e.g. "wash basin promo" vs a name with extra words).
    if not rows:
        words = [w for w in tok.split() if len(w) >= 3]
        if words:
            q = base
            for w in words:
                ws = f"%{w}%"
                q = q.filter(
                    or_(
                        Product.product_name.ilike(ws),
                        Product.description.ilike(ws),
                    )
                )
            rows = q.limit(limit).all()
    out: list[dict[str, Any]] = []
    for pid, code, name, brand_id, brand_code, brand_name in rows:
        out.append(
            {
                "entity_type": "product",
                "canonical_code": code,
                "uuid": str(pid) if pid else None,
                "match_field": "brand_access",
                "match_tier": "brand_access_fallback",
                "similarity": None,
                "display": {
                    "product_name": name,
                    "via_token": tok,
                    "brand": {
                        "brand_id": str(brand_id) if brand_id else None,
                        "brand_code": brand_code,
                        "brand_name": brand_name,
                    },
                },
            }
        )
    return out


def _caller_wants_products(allowed_entity_types: list[str] | None) -> bool:
    """True only when the caller's RAW whitelist canonicalizes to `product`.

    Brand / category aliases also canonicalize to `product` via
    `_DOMAIN_HINT_EXPANSIONS`, but the caller did NOT explicitly ask for
    products in those cases - they want promotion headers. We use this flag
    to gate the through-promotion product expansion so a `brand` hint with
    matched promotions stays at promotion-level only.
    """
    if not allowed_entity_types:
        return False
    from app.services.entity_resolver import _canonical_entity_type as _canon  # local to avoid cycles
    for raw in allowed_entity_types:
        if (raw or "").strip().lower() in {"product", "products", "master_product", "master_products", "product_attachment", "product_attachments"}:
            return True
    return False


def _expand_products_via_promotions(
    db: Session,
    result: dict[str, Any],
    tokens: list[str],
    access_level_names: list[str] | None,
    wants_products: bool = True,
) -> dict[str, Any]:
    """Replace product matches with products surfaced via the matched promos.

    Strategy when domain_hint=promotion:
      1. For each token, find promotion UUIDs whose description matches.
      2. Optionally filter promos by access_levels intersection.
      3. Look up all products in those promotions.
      4. Replace the token's `product` matches with these - they are the
         products the user actually wants ("send me water closet promotion"
         means "give me products in water-closet promos").

    Non-product entities (attachments, promotions themselves, etc.) stay
    untouched so the agent can still surface promo headers / flyers alongside
    the products.
    """
    if not isinstance(result, dict):
        return result

    # Translate access-level names to codes once.
    allowed_codes: set[str] = set()
    if access_level_names:
        names_lower = {n.strip().lower() for n in access_level_names if n and n.strip()}
        if names_lower:
            allowed_codes = {
                r[0]
                for r in db.query(ContactAccessType.code)
                .filter(func.lower(ContactAccessType.name).in_(names_lower))
                .all()
            }

    new_result = dict(result)

    # Ordering rule: domain_hint=promotion makes promotion the highest-priority
    # entity. Surface promotion entries FIRST (synthesized from matched
    # promo UUIDs, dedup-safe vs ones already present), then the
    # through-promotion products. Non-promotion / non-product entities (e.g.
    # attachments) keep their original position at the tail.
    if isinstance(result.get("resolutions"), list):
        new_resolutions = []
        for tr in result["resolutions"]:
            tok = tr.get("token") or ""
            promo_ids = _resolve_promotion_ids_for_token(db, tok, allowed_codes or None)
            promo_matches = _build_promotion_resolutions(db, promo_ids)

            existing = tr.get("matches", []) or []
            existing_promo_uuids = {
                str(m.get("uuid"))
                for m in existing
                if m.get("entity_type") == "promotion" and m.get("uuid")
            }
            kept_other = [
                m for m in existing
                if m.get("entity_type") not in ("product", "promotion")
            ]
            kept_existing_promos = _apply_serving_policy_to_promo_matches(
                db, [m for m in existing if m.get("entity_type") == "promotion"]
            )
            # The resolved products are KEPT - a product SKU must still resolve
            # under domain_hint=promotion (the expander used to wipe them, so a
            # valid SKU returned empty just because it wasn't a promo name).
            kept_products = [
                m for m in existing if m.get("entity_type") == "product"
            ]
            new_promo_matches = [
                m for m in promo_matches if str(m.get("uuid")) not in existing_promo_uuids
            ]

            # Reverse membership: promotions that CONTAIN the resolved products.
            # This is how "check the promo for <SKU>" gets answered - the SKU
            # can't match a promo description, so walk promotion_products back.
            product_uuids = {
                str(m.get("uuid")) for m in kept_products if m.get("uuid")
            }
            seen_promo_uuids = existing_promo_uuids | {
                str(m.get("uuid")) for m in new_promo_matches
            }
            member_promo_matches = [
                m
                for m in _build_promotions_for_products(
                    db, product_uuids, allowed_codes or None
                )
                if str(m.get("uuid")) not in seen_promo_uuids
            ]

            # Promo-first short-circuit: when any promotion matches exist
            # (existing, name-synthesized, or via product membership), DON'T
            # enumerate through-promotion products - the caller's products are
            # already kept above, and enumeration would be noise.
            has_promo = bool(
                kept_existing_promos or new_promo_matches or member_promo_matches
            )
            if has_promo:
                product_matches: list[dict[str, Any]] = []
            elif promo_ids and wants_products:
                product_matches = _build_product_resolutions_from_promotions(
                    db, tok, promo_ids
                )
            elif not promo_ids and allowed_codes:
                # Rescue path: no promo for this token under active access
                # levels → surface products from brands the contact can see.
                # Runs even when caller didn't ask for products so the agent
                # has SOMETHING to answer with rather than empty.
                product_matches = _resolve_products_by_brand_access(
                    db, tok, allowed_codes
                )
            else:
                product_matches = []

            # Dedup through-promo products against the ones we already kept.
            kept_product_uuids = {
                str(m.get("uuid")) for m in kept_products if m.get("uuid")
            }
            product_matches = [
                m for m in product_matches
                if str(m.get("uuid")) not in kept_product_uuids
            ]

            new_matches = (
                kept_existing_promos
                + new_promo_matches
                + member_promo_matches
                + kept_products
                + product_matches
                + kept_other
            )
            new_tr = dict(tr)
            new_tr["matches"] = new_matches
            new_tr["resolved"] = len(new_matches) == 1 and not tr.get("ambiguous", False)
            if len(new_matches) > 1:
                new_tr["ambiguous"] = True
            new_resolutions.append(new_tr)
        new_result["resolutions"] = new_resolutions
        new_result["unresolved_tokens"] = [
            r["token"] for r in new_resolutions if not (r.get("matches") or [])
        ]

    if isinstance(result.get("intersection"), list):
        # AND-mode: INTERSECT promo sets across tokens. Caller chose AND mode
        # because they want promotions matching EVERY token ("kitchen tap"
        # AND "Sorento"). Earlier union behaviour returned the OR of token
        # results which surfaced unrelated Sorento promos.
        promo_sets: list[set[str]] = [
            _resolve_promotion_ids_for_token(db, tok, allowed_codes or None)
            for tok in tokens
            if tok
        ]
        if promo_sets:
            union_promos = set.intersection(*promo_sets)
        else:
            union_promos = set()
        promo_matches = _build_promotion_resolutions(db, union_promos)
        existing_inter = result["intersection"] or []
        existing_promo_uuids = {
            str(m.get("uuid"))
            for m in existing_inter
            if m.get("entity_type") == "promotion" and m.get("uuid")
        }
        kept_other = [
            m for m in existing_inter
            if m.get("entity_type") not in ("product", "promotion")
        ]
        kept_existing_promos = _apply_serving_policy_to_promo_matches(
            db, [m for m in existing_inter if m.get("entity_type") == "promotion"]
        )
        # Keep resolved products (see OR-branch rationale) and walk membership.
        kept_products = [
            m for m in existing_inter if m.get("entity_type") == "product"
        ]
        new_promo_matches = [
            m for m in promo_matches if str(m.get("uuid")) not in existing_promo_uuids
        ]
        inter_product_uuids = {
            str(m.get("uuid")) for m in kept_products if m.get("uuid")
        }
        inter_seen_promo_uuids = existing_promo_uuids | {
            str(m.get("uuid")) for m in new_promo_matches
        }
        member_promo_matches = [
            m
            for m in _build_promotions_for_products(
                db, inter_product_uuids, allowed_codes or None
            )
            if str(m.get("uuid")) not in inter_seen_promo_uuids
        ]
        # Same promo-first short-circuit as the OR-mode branch above.
        has_promo = bool(
            kept_existing_promos or new_promo_matches or member_promo_matches
        )
        if has_promo:
            product_matches = []
        elif union_promos and wants_products:
            product_matches = _build_product_resolutions_from_promotions(
                db, " ".join(tokens), union_promos
            )
        elif not union_promos and allowed_codes:
            # AND-mode brand-access fallback: intersect per-token brand-access
            # product sets so the answer set still respects every token.
            per_token_sets: list[list[dict[str, Any]]] = []
            for tok in tokens:
                if not tok:
                    continue
                per_token_sets.append(
                    _resolve_products_by_brand_access(db, tok, allowed_codes)
                )
            if not per_token_sets:
                product_matches = []
            else:
                shared_ids: set[str] | None = None
                for lst in per_token_sets:
                    ids = {str(m["uuid"]) for m in lst if m.get("uuid")}
                    shared_ids = ids if shared_ids is None else shared_ids & ids
                product_matches = []
                if shared_ids:
                    seen: set[str] = set()
                    for lst in per_token_sets:
                        for m in lst:
                            uid = str(m.get("uuid") or "")
                            if uid in shared_ids and uid not in seen:
                                seen.add(uid)
                                product_matches.append(m)
        else:
            product_matches = []
        inter_kept_uuids = {
            str(m.get("uuid")) for m in kept_products if m.get("uuid")
        }
        product_matches = [
            m for m in product_matches
            if str(m.get("uuid")) not in inter_kept_uuids
        ]
        new_intersection = (
            kept_existing_promos
            + new_promo_matches
            + member_promo_matches
            + kept_products
            + product_matches
            + kept_other
        )
        new_result["intersection"] = new_intersection
        new_result["empty"] = not new_intersection

    return new_result


def _filter_products_in_promotions(db: Session, result: dict[str, Any]) -> dict[str, Any]:
    """When domain_hint=promotion, drop product matches not linked to any promotion.

    The agent's intent under a promotion domain is "find products that have an
    active promo" - surfacing products that aren't in any `promotion_products`
    row wastes context. Walks resolutions + intersection, collects product
    UUIDs, queries `promotion_products.product_id` once, and filters out the
    misses. Non-product entities untouched. Idempotent: re-running on an
    already-filtered payload is a no-op.
    """
    if not isinstance(result, dict):
        return result

    # Collect product UUIDs across both shapes.
    product_ids: set[str] = set()

    def _collect_from_matches(matches: Any) -> None:
        if not isinstance(matches, list):
            return
        for m in matches:
            if isinstance(m, dict) and m.get("entity_type") == "product" and m.get("uuid"):
                product_ids.add(str(m["uuid"]))

    for tr in result.get("resolutions", []) or []:
        _collect_from_matches(tr.get("matches"))
    _collect_from_matches(result.get("intersection"))

    if not product_ids:
        return result

    rows = (
        db.query(PromotionProduct.product_id)
        .filter(PromotionProduct.product_id.in_(product_ids))
        .distinct()
        .all()
    )
    kept_ids: set[str] = {str(r[0]) for r in rows if r[0]}

    if kept_ids == product_ids:
        return result  # everything already linked - no-op

    def _filter_matches(matches: Any) -> list[Any]:
        if not isinstance(matches, list):
            return matches if matches is not None else []
        out: list[Any] = []
        for m in matches:
            if (
                isinstance(m, dict)
                and m.get("entity_type") == "product"
                and m.get("uuid")
                and str(m["uuid"]) not in kept_ids
            ):
                continue
            out.append(m)
        return out

    new_result = dict(result)
    new_resolutions: list[dict[str, Any]] = []
    for tr in result.get("resolutions", []) or []:
        new_tr = dict(tr)
        new_matches = _filter_matches(tr.get("matches"))
        new_tr["matches"] = new_matches
        new_tr["resolved"] = len(new_matches) == 1 and not tr.get("ambiguous", False)
        new_resolutions.append(new_tr)
    if new_resolutions:
        new_result["resolutions"] = new_resolutions
        new_result["unresolved_tokens"] = [
            r["token"] for r in new_resolutions if not (r.get("matches") or [])
        ]
    if "intersection" in result:
        new_result["intersection"] = _filter_matches(result.get("intersection"))
        new_result["empty"] = not new_result["intersection"]
    return new_result


def _apply_promotion_access_levels_filter(
    db: Session,
    result: dict[str, Any],
    access_level_names: list[str] | None,
) -> dict[str, Any]:
    """Drop promotion / attachment matches whose `access_levels` JSONB does not
    intersect the caller-supplied access-level NAMES.

    Names are translated to canonical `contact_access_types.code` values (case-
    insensitive name match) before intersection, because both
    `Promotion.access_levels` and `Attachment.access_levels` store codes. Other
    entity types are untouched. Empty / missing `access_level_names` is a no-op.
    """
    if not access_level_names:
        return result
    names_lower = {n.strip().lower() for n in access_level_names if n and n.strip()}
    if not names_lower:
        return result

    code_rows = (
        db.query(ContactAccessType.code)
        .filter(func.lower(ContactAccessType.name).in_(names_lower))
        .all()
    )
    allowed_codes: set[str] = {r[0] for r in code_rows}

    def _collect_ids_by_type() -> dict[str, set[str]]:
        bucket: dict[str, set[str]] = {t: set() for t in _ACCESS_FILTERED_TYPES}
        for tr in result.get("resolutions", []) or []:
            for m in tr.get("matches", []) or []:
                et = m.get("entity_type")
                if et in _ACCESS_FILTERED_TYPES and m.get("uuid"):
                    bucket[et].add(str(m["uuid"]))
        for m in result.get("intersection", []) or []:
            et = m.get("entity_type")
            if et in _ACCESS_FILTERED_TYPES and m.get("uuid"):
                bucket[et].add(str(m["uuid"]))
        return bucket

    ids_by_type = _collect_ids_by_type()
    keep_by_type: dict[str, set[str]] = {t: set() for t in _ACCESS_FILTERED_TYPES}

    if allowed_codes and ids_by_type["promotion"]:
        for pid, levels in (
            db.query(Promotion.id, Promotion.access_levels)
            .filter(Promotion.id.in_(list(ids_by_type["promotion"])))
            .all()
        ):
            if isinstance(levels, list) and allowed_codes.intersection(levels):
                keep_by_type["promotion"].add(str(pid))

    if allowed_codes and ids_by_type["attachment"]:
        for aid, levels in (
            db.query(Attachment.id, Attachment.access_levels)
            .filter(Attachment.id.in_(list(ids_by_type["attachment"])))
            .all()
        ):
            if isinstance(levels, list) and allowed_codes.intersection(levels):
                keep_by_type["attachment"].add(str(aid))

    def _keep(m: dict[str, Any]) -> bool:
        et = m.get("entity_type")
        if et not in _ACCESS_FILTERED_TYPES:
            return True
        return str(m.get("uuid") or "") in keep_by_type.get(et, set())

    if "resolutions" in result:
        for tr in result["resolutions"]:
            tr["matches"] = [m for m in tr.get("matches", []) if _keep(m)]
            tr["resolved"] = bool(tr["matches"]) and not tr.get("ambiguous", False)
        result["unresolved_tokens"] = [
            tr["token"] for tr in result["resolutions"] if not tr["matches"]
        ]

    if "intersection" in result:
        result["intersection"] = [m for m in result["intersection"] if _keep(m)]
        by_type: dict[str, list[dict[str, Any]]] = {}
        for m in result["intersection"]:
            by_type.setdefault(m["entity_type"], []).append(m)
        result["by_entity_type"] = by_type
        result["empty"] = not result["intersection"]
        if result["empty"]:
            result["unresolved_tokens"] = list(result.get("tokens", []))

    return result


class ResolveReferenceRequest(BaseModel):
    """POST body for /references/resolve."""

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(default="", description="Free-text query or a single code to resolve.")
    tokens: list[str] | None = Field(
        default=None,
        description="Optional pre-extracted tokens. If provided, resolver skips regex extraction.",
    )
    raw_tokens: list[str] | None = Field(
        default=None,
        description=(
            "Positionally parallel to `tokens`: `raw_tokens[i]` is the pre-fold "
            "raw text (with spaces) that produced `tokens[i]` - e.g. "
            "tokens=['SRTWB8004BASINTAP'], raw_tokens=['SRTWB8004 BASIN TAP']. "
            "Ignored unless `tokens` is also given and both lists have equal "
            "length. Used only by the server-side product head-code retry - "
            "when a folded token exact-misses, the resolver re-probes the "
            "leading code-shaped word of its raw text (e.g. n8n folds a "
            "spoken/typed 'code + description' into one token before calling "
            "resolve). Absent or null = response byte-identical to today."
        ),
    )
    match_mode: str = Field(
        default="or",
        description=(
            "'or' (default): each token resolves independently - per-token candidate lists, "
            "ambiguity surfaced per token. 'and': cross-token intersection - returns ONLY rows "
            "whose concatenated searchable columns contain EVERY token. Use 'and' for compound "
            "filters like 'cabana filter tap promotion'. Code-only entity types (SPO / GRN / "
            "inbound shipment) are skipped in 'and' mode."
        ),
    )
    allowed_entity_types: list[str] | None = Field(
        default=None,
        description=(
            "Restrict resolution to these entity types. Always treated as a set "
            "filter / whitelist: every token is probed against EVERY supplied type "
            "(cross product). Example - tokens=['Sorento water closet','latest "
            "promo'] with allowed_entity_types=['product','promotion'] resolves "
            "both tokens against both product and promotion probes. Callers that "
            "need 1:1 positional pairing (token[i] resolved only against "
            "allowed_entity_types[i]) must issue one resolve call per pair.\n\n"
            "Canonical types: product, customer_order, customer, inbound_shipment, "
            "spo_allocation, grn, warehouse, supplier, promotion, transporter, "
            "form, attachment, attachment_type. `attachment_type` resolves a doc-"
            "class label (e.g. 'catalogue', 'brochure', 'spec sheet') to the "
            "AttachmentType UUID for downstream `attachment_type_ids` filters; "
            "pure-alpha labels bypass the code-token regex so callers may pass "
            "them via `query` (auto-split on whitespace) or `tokens` explicitly."
        ),
    )
    access_levels: list[str] | None = Field(
        default=None,
        description=(
            "Post-filter for promotion AND attachment matches. Array of contact-"
            "access-type NAMES (e.g. ['Dealer','End User']) - translated to codes "
            "via contact_access_types.name and intersected with the entity's "
            "`access_levels` JSONB column. A match survives only when at least one "
            "of its stored access codes is in the translated set. Non-promotion / "
            "non-attachment matches are untouched. Empty / null → no-op."
        ),
    )
    fallback_to_all_types: bool = Field(
        default=False,
        description=(
            "Safety net for wrong entity-type guesses by the upstream agent. When "
            "`allowed_entity_types` is set AND the first pass yields zero resolved "
            "tokens, the resolver re-runs with `allowed_entity_types=None` and tags "
            "the response with `fallback_applied: true` + `fallback_types_found` "
            "(the entity types of the recovered matches). Lets the agent recover "
            "from picking 'order' when the user actually meant 'customer'."
        ),
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description=(
            "Cap returned matches. Applied per-token (each resolution's "
            "`matches` truncated to `limit`) AND to `intersection` in AND-mode. "
            "Omit / null = no cap (current default behaviour)."
        ),
    )
    exact_match: bool = Field(
        default=False,
        description=(
            "Only with `spec_fallback`: list only products that hold every value asked "
            "for exactly, the product type included, never the nearest by relevance "
            "(the chatbot's rule, PR #833 fix round 10). Absent or false keeps the "
            "relevance ranking byte-identical for every other caller."
        ),
    )
    spec_fallback: bool = Field(
        default=False,
        description=(
            "Opt in to spec search. When true AND the normal (code-only) product "
            "probes return zero matches, the resolver additionally ranks the catalog "
            "against `extracted_specs` + `free_terms` and returns `spec_candidates` "
            "plus `floor_missed`, leaving `resolutions` untouched. Two honesty "
            "fields come with it, and they are DIFFERENT sentences: `spec_unmet` is "
            "a known key nothing offered can satisfy ('thickness isn't recorded for "
            "these'), `unrecognized_terms` is a word that named nothing at all ('I "
            "don't know what X means'). Absent or false "
            "means the response is byte-identical to today, so the feature is inert "
            "for every existing caller. It is a FALLBACK: it never runs when the "
            "normal probes already resolved something the question can be "
            "answered with - on an inventory / master_products / "
            "product_attachment question (`domain`), that means a product row; "
            "a promotion whose description matched does not count there."
        ),
    )
    extracted_specs: list[dict] | None = Field(
        default=None,
        description=(
            "Specs the parser read out of the customer message, as "
            "[{key, value, evidence}]. Keys and values must come from the Spec "
            "Registry (`GET /api/v1/master-data/spec-registry`). Every spec is a "
            "scoring BOOST, never a filter, so an over-extracted one cannot empty the "
            "picker. Only used when `spec_fallback` is true."
        ),
    )
    unknown_values: list[dict] | None = Field(
        default=None,
        description=(
            "Fix round 8 on PR #833: descriptors the chatbot's grounding step "
            "(`chatbot/head/grounding.py`) could not place on any choice of the "
            "specification registry, as [{key, label, said, known}] ('pink' said as a "
            "finish or colour). Answered like `unknown_spec_values`: said back with "
            "the known choices before anything is counted or searched."
        ),
    )
    set_list_max: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Fix round 9 on PR #833: the longest set the caller lists in one reply. A "
            "`require` set longer than it carries `predicate.breakdown` (its members "
            "counted by the next attribute), and a set that qualifies nothing carries "
            "`predicate.members` (the described products without the legs). Absent: "
            "neither is computed."
        ),
    )
    hidden_spec_keys: list[str] | None = Field(
        default=None,
        description=(
            "PLAN-spec-visibility-policy.md AC-18: registry keys this contact may "
            "not be told about (`ctx.access.hidden_spec_keys`). Dropped from the "
            "derived spec search inputs BEFORE ranking - so a hidden key never "
            "decides which product wins - and stripped from every candidate's "
            "`specifications` / `matched_specs` before the response is built, so "
            "the value cannot leak through even when the product's OWN stored "
            "values carry it. Only used when `spec_fallback` is true. UNIONED "
            "with the server-resolved set below when `contact_id` is also sent - "
            "a caller-supplied list only ever NARROWS what search_specs prints, "
            "it can never widen past what the contact's own policy hides."
        ),
    )
    contact_id: str | None = Field(
        default=None,
        description=(
            "Security review B/S2: when present, the route resolves THIS "
            "contact's spec visibility policy itself (`resolve_policy` + "
            "`hidden_keys`, the same pair `check_access` uses) rather than "
            "trusting only the caller-supplied `hidden_spec_keys` - a caller "
            "that forgot to compute or forward the hidden set (or sent a stale "
            "one) must not leak a hidden key. `respond_contacts.id` or the "
            "Respond.io id; `space_id` disambiguates the latter. Only used "
            "when `spec_fallback` is true."
        ),
    )
    space_id: str | None = Field(
        default=None,
        description="Respond.io workspace id, to disambiguate a Respond.io `contact_id`.",
    )
    free_terms: list[str] | None = Field(
        default=None,
        description=(
            "Leftover customer words that did not map onto a registry key, e.g. "
            "['kitchen sink']. Matched against the rendered spec sentence and the "
            "class synonyms, NEVER against products.description. Only used when "
            "`spec_fallback` is true."
        ),
    )
    require: dict | None = Field(
        default=None,
        description=(
            "Shape B: a domain predicate over the DESCRIBED set (\"what faucets "
            "have certs\"). Keys - `attachment_type` (the customer's LABEL, e.g. "
            "'technical drawing', resolved server-side), `certificate` (true, or "
            "{scheme, validity_state}), `promotion` (true), `stock` (true = "
            "on-hand > 0). Multiple keys AND. The intersection with the class "
            "set named by `free_terms` is computed inside the CRM over the full "
            "company-scoped catalogue, so the count is honest - never a top-K "
            "join across the wire. Response gains a `predicate` block "
            "(qualifying_total / truncated / unrecognized_terms) and the "
            "qualifying top-K lands in `resolutions[].matches` with "
            "`match_tier='spec_search'`. Absent = response byte-identical to "
            "today. When present it supersedes `spec_fallback`."
        ),
    )
    scope_terms: list[str] | None = Field(
        default=None,
        description=(
            "Words that SCOPE the described set without ranking it - the class word "
            "the caller's own parser already identified ('tap', 'kitchen sink'), sent "
            "as a value rather than left to be sliced back out of `query`. Membership "
            "only: unlike `free_terms` these never drive `search_specs` ranking, so a "
            "product that qualifies purely through an id match is not evicted by the "
            "ranker's evidence floor. Only used when `require` is present."
        ),
    )
    predicate_words: list[str] | None = Field(
        default=None,
        description=(
            "Words `require` already accounts for (a leg's own name - 'stock', "
            "'incoming', 'promotion' - or an attachment_type entity's raw, "
            "e.g. 'cert', 'photo') - stripped from `query` before the described "
            "set is derived from it, so 'which sorento bidet has cert' does not "
            "also ask the spec reader to bind the word 'cert'. Only used when "
            "`require` is present."
        ),
    )
    understand_phrase: bool = Field(
        default=False,
        description=(
            "Read `query` with a language model before ranking, so a phrasing nobody "
            "wrote a synonym for still lands on a spec. OFF by default because it "
            "costs 2-3 SECONDS on the reply path - a real wait for a customer.\n\n"
            "When on, the response carries `semantic_used` and `semantic_ms` so the "
            "caller can tell the customer WHY the reply is slow ('looking properly, "
            "one moment') instead of leaving them watching nothing happen. "
            "Only used when `spec_fallback` is true."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "The caller is a TEST or console run and must leave no trace. The resolution "
            "itself is UNCHANGED - same probes, same spec search, same model call, same "
            "response - and the only thing this suppresses is the "
            "`ai_assistant_usage_logs` bookkeeping row the spec-search reader writes.\n\n"
            "Exists for the chatbot turn engine's D14 rule (a turn flagged `is_test` or "
            "carrying a `test_run_id` writes ZERO rows outside `chatbot.turns`); this "
            "endpoint was the one write its business lane could still reach. Deliberately "
            "NOT a 'skip the work' flag: a dry run has to answer identically to a live "
            "one, or the shadow comparison it exists for compares two behaviours."
        ),
    )
    domain_hint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("domain_hint", "domain"),
        description=(
            "Accepts `domain_hint` or its alias `domain`. "
            "Scope the resolution to attachments of a specific AttachmentType "
            "(matched case-insensitively against `code` then `type_name`, with "
            "catalog ↔ catalogue spelling variants). When set, the resolver "
            "SHORT-CIRCUITS the normal entity probes and ILIKE-matches the "
            "supplied tokens / query against `Attachment.original_filename` / "
            "`description` filtered to that type. Returns ResolvedEntity rows "
            "with `entity_type='attachment'` whose UUIDs feed downstream tools "
            "like `crm_resource_attachments_catalogue`. Example: domain_hint="
            "'catalogue' + tokens=['Sorento'] returns catalogue attachments "
            "containing 'Sorento'."
        ),
    )
    entity_pins: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional per-token uuid pin: token (as it appears in `tokens`, "
            "the ORIGINAL text the caller sent - matched through the same "
            "entity-stopword stripping resolution itself applies) -> the "
            "exact row uuid the caller already picked for it (e.g. from a "
            "prior disambiguation reply). Codes are unique only PER COMPANY, "
            "so re-deriving from the bare code alone can reopen an ambiguity "
            "the caller already closed (n8n exec 14659385 customer 300-C043, "
            "exec 14661446 product SRTBV110-DIY - both cross-company). When "
            "the pinned uuid is among the token's own matches AFTER normal "
            "company-scope filtering, resolution narrows to exactly that "
            "row. Every other outcome is the SAME explicit 400 "
            "(`ENTITY_PIN_MISMATCH`) - a uuid absent from the token's scoped "
            "matches (whether it names nothing at all or names a real row "
            "the caller's own company scope cannot see - the two are "
            "deliberately NOT told apart), a blank pin value, an unparseable "
            "uuid, or a key that does not correspond to any token in this "
            "request. Never a silent misresolution and never a silent "
            "fallback to plain token matching. Absent or empty = "
            "byte-identical to today. POST only (this field does not exist "
            "on the GET variant); rejected with a 400 under "
            "`match_mode='and'` (AND-mode intersection has no per-token view "
            "to pin)."
        ),
    )

    @field_validator("contact_id", mode="before")
    @classmethod
    def _contact_id_to_string(cls, v: Any) -> Any:
        """The Respond.io contact id is a NUMBER on the wire (both the chatbot
        lane and n8n forward the webhook value as-is), so coerce it before the
        string field rejects it."""
        if isinstance(v, int) and not isinstance(v, bool):
            return str(v)
        return v


#: The resolver tiers that match a product CODE (`entity_resolver`): the whole code, a
#: code prefix, a code substring, a set's head code, the AND probe (code-only by design).
#: Never `embedding`, `trgm` or `spec_search`: those are nearest-neighbour guesses.
_CODE_MATCH_TIERS = frozenset({"exact", "prefix", "substring", "head_code", "and"})


def _code_matched(result: dict[str, Any], tokens: list[str] | None = None) -> bool:
    """Code first (fix round 13 on PR #833, owner 28 Sep 2026: "if found by product code
    -> forward asking, if cannot find product code, fallback to spec search -> reverse
    asking"): did the resolver match a caller token to a product BY ITS CODE?

    A match by code is a code tier (`_CODE_MATCH_TIERS`, at any of them) whose own code
    contains the token, case and separators ignored: "srtwc286" by prefix, "7820" as a
    substring of all seven MKT7820SS codes. Such a token makes the turn a forward ask
    over every code match, so `require` (the HAS / counted-set branch) never runs for
    it and zero on hand stays in the answer. Only a token with a digit in it can be a
    code: a word ("basin", "gunmetal", "sorento" even where a product is literally
    coded SORENTO) describes a set and keeps the counted-set path.

    `resolutions` carry their token; AND mode's `intersection` blends every token into
    one list, so the caller's own `tokens` are tested against it instead.
    """

    def _fold(value: Any) -> str:
        return re.sub(r"[^0-9a-z]", "", str(value or "").lower())

    def _hit(token: Any, match: Any) -> bool:
        fragment = _fold(token)
        return (
            any(ch.isdigit() for ch in fragment)
            and isinstance(match, dict)
            and match.get("entity_type") == "product"
            and match.get("match_tier") in _CODE_MATCH_TIERS
            and fragment in _fold(match.get("canonical_code"))
        )

    for resolution in result.get("resolutions") or []:
        token = (resolution or {}).get("token")
        if any(_hit(token, m) for m in (resolution or {}).get("matches") or []):
            return True
    return any(
        _hit(token, match)
        for token in tokens or []
        for match in result.get("intersection") or []
    )


def _collect_lookup_product_ids(result: dict[str, Any]) -> list[str]:
    """Every product uuid LOOKUP matched BY CODE, in order, deduped.

    Work item C2: the described set's other half besides the class/product_type/
    brand bindings.

    Fix round 10 on PR #833 (owner, 28 Sep 2026: "i tried to search like gunmetal
    basin, there is no such thing and it gives me flexible trap", "for #833 yeah exact
    only"): only a code-tier match counts (the code, its prefix, a substring of it: the
    code matching a typed "65502" still gets). A word ("basin", "gunmetal basin")
    describes the set through the catalogue's own vocabulary alone (`filter_specs`); the
    resolver's embedding and trigram neighbours of a word are a guess, and the embedding
    tier is what put a flexible trap into "gunmetal basin".
    """
    ids: list[str] = []
    seen: set[str] = set()

    def _take(match: Any) -> None:
        if not isinstance(match, dict) or match.get("entity_type") != "product":
            return
        if match.get("match_tier") not in _CODE_MATCH_TIERS:
            return
        uid = match.get("uuid")
        if uid and uid not in seen:
            seen.add(uid)
            ids.append(uid)

    for resolution in result.get("resolutions") or []:
        for match in (resolution or {}).get("matches") or []:
            _take(match)
    for match in result.get("intersection") or []:
        _take(match)
    return ids


#: The resolver tiers that answer a token with its nearest neighbour, not a match.
_NEIGHBOUR_TIERS = frozenset({"embedding", "trgm"})


def _drop_word_neighbours(result: dict[str, Any]) -> None:
    """Fix round 10 on PR #833 (owner, 28 Sep 2026: "i tried to search like gunmetal
    basin, there is no such thing and it gives me flexible trap", "for #833 yeah exact
    only"), for `exact_match` callers: a WORD token (no digit in it: "basin", "gunmetal
    basin") gets no product by nearest neighbour, neither as a match (the embedding tier
    answered "basin" with the flexible trap) nor as a did-you-mean alternative. A token
    with a digit keeps them: a code typo ("SRWC8088") still finds its code. Customers,
    orders and every other entity type are untouched."""
    def word(token: Any) -> bool:
        return not any(ch.isdigit() for ch in str(token or ""))

    def neighbour(match: Any) -> bool:
        return (
            isinstance(match, dict)
            and match.get("entity_type") == "product"
            and match.get("match_tier") in _NEIGHBOUR_TIERS
        )

    for resolution in result.get("resolutions") or []:
        if not isinstance(resolution, dict) or not word(resolution.get("token")):
            continue
        for key in ("matches", "alternatives"):
            kept = [m for m in resolution.get(key) or [] if not neighbour(m)]
            if len(kept) != len(resolution.get(key) or []):
                resolution[key] = kept
        if not resolution.get("matches"):
            resolution["resolved"] = False
            token = resolution.get("token")
            unresolved = result.setdefault("unresolved_tokens", [])
            if token and token not in unresolved:
                unresolved.append(token)
    if all(word(t) for t in result.get("tokens") or []):
        if isinstance(result.get("alternatives"), list):
            result["alternatives"] = [m for m in result["alternatives"] if not neighbour(m)]
        if isinstance(result.get("intersection"), list):
            result["intersection"] = [m for m in result["intersection"] if not neighbour(m)]
            by_type: dict[str, list[dict[str, Any]]] = {}
            for m in result["intersection"]:
                by_type.setdefault(m["entity_type"], []).append(m)
            result["by_entity_type"] = by_type
            result["empty"] = not result["intersection"]
            if result["empty"]:
                result["unresolved_tokens"] = list(result.get("tokens") or [])


# A COPY of `app.services.chatbot.head.output_exchange._CERT_RE`, never an import of it:
# this file sits outside the chatbot module boundary (`tests/chatbot/test_import_boundary
# .py`'s AC-002), so the same cert-word test is duplicated here rather than reached across
# it. Keep the two in lockstep by hand if the word list ever changes.
_CERT_WORD_RE = re.compile(r"cert|ikram|span|sirim|bomba|ms\s?[0-9]|halal", re.IGNORECASE)

# A COPY of `app.services.chatbot.lanes.business.answer.SET_ID_CAP`, for the same
# module-boundary reason `_CERT_WORD_RE` above is a copy: a counted set lists (up to
# `answer.SET_LIST_MAX`) off however many qualifying ids `resolve_product_set` is asked
# for, so this file has to ask for at least this many rather than the ordinary LOOKUP
# page size.
_SET_ID_CAP = 200


def _stock_policy_for(db: Session, payload: "ResolveReferenceRequest"):
    """The asking contact's stock visibility policy, or None when no contact is named.

    Resolved through the same NULL-workspace fallback the spec policy uses below (SF-1),
    so a contact with `workspace_id IS NULL` gets their real policy rather than none.
    """
    if not payload.contact_id:
        return None
    from app.services.field_access import resolve_contact_with_null_workspace_fallback
    from app.services.stock_visibility import resolve_policy

    resolved = resolve_contact_with_null_workspace_fallback(
        db, contact_id=payload.contact_id, space_id=payload.space_id
    )
    return resolve_policy(db, resolved or payload.contact_id, payload.space_id)


def _with_grounded_unknowns(found: list[dict], grounded: list[dict] | None) -> list[dict]:
    """The registry's own unknown-value reading of the message, plus the ones the
    chatbot's grounding step found (fix round 8 on PR #833), one per key. Grounding wins
    for a key both name: it read the parser's placed words, not the whole sentence."""
    extra = [dict(u) for u in (grounded or []) if isinstance(u, dict) and str(u.get("said") or "").strip()]
    keys = {u.get("key") for u in extra if u.get("key")}
    return extra + [u for u in found if u.get("key") not in keys]


def _unknown_value_members(db: Session, payload, query_text: str, unknown: list[dict], current_user) -> dict | None:
    """The described set without its unknown value, broken down by that value's key:
    "pink water closets" is answered with the finishes the water closets do come in
    (fix round 9 on PR #833, owner: "always break it down"). None when the rest of the
    ask describes no set."""
    from app.services.product_predicate_service import resolve_product_set
    from app.services.product_spec_understanding import derive_search_inputs

    # Fix round 10 on PR #833: a word with no key at all ("pnk" in "pnk water closet",
    # "kitchne" in "kitchne sink") breaks the rest down by the next attribute that splits
    # it (`_set_breakdown`), so the reply still lists what does exist.
    key = next((u.get("key") for u in unknown if u.get("key")), None)
    said = [str(u.get("said") or "") for u in unknown]
    specs, _free, _excl, _understanding = derive_search_inputs(
        db,
        _strip_predicate_words(query_text, said),
        specs=list(payload.extracted_specs or []),
        free_terms=[],
        allow_model=False,
        user_id=current_user.get("id"),
        log_usage=False,
    )
    brand_entry = next((e for e in specs if e.get("key") == "brand"), None)
    specs = [e for e in specs if e.get("key") not in ("brand", key)]
    scope_terms = list(payload.scope_terms or [])
    if not specs and not scope_terms:
        return None
    outcome = resolve_product_set(
        db,
        require={},
        specs=specs,
        scope_terms=scope_terms or None,
        limit=1,
        brand=str(brand_entry["value"]) if brand_entry else None,
        access_levels=payload.access_levels,
        breakdown_key=key,
        breakdown_over=None if key else 0,
    )
    if not outcome.get("qualifying_total"):
        return None
    members: dict[str, Any] = {"total": int(outcome["qualifying_total"])}
    for field in ("breakdown", "description", "class_labels"):
        if outcome.get(field):
            members[field] = outcome[field]
    return members


def _strip_predicate_words(text: str, words: list[str] | None) -> str:
    """`query` with every `predicate_words` entry removed, whole-word, case-insensitive.

    So "which sorento bidet has cert" does not also ask the described-set reader to
    bind the word "cert" it was for the PREDICATE, not the description.

    REV-S4/AC-1332 (third console pass): each entry is split on whitespace and
    stripped WORD BY WORD, never matched as one contiguous phrase - the parser's
    own normalisation can widen a word ("cert" to "certificate") so the phrase
    "PPS cert" is no longer contiguous in "which item has PPS certificate", and a
    single whole-phrase regex (with its own trailing word-boundary assertion)
    then fails to match at all, leaving "PPS" in the remainder as an
    unrecognized leftover. Compiled inline (REV-S2): the pattern is built from a
    customer-controlled string, so caching it by that string is an unbounded,
    caller-fed dict.
    """
    stripped = text or ""
    for phrase in words or []:
        for word in re.split(r"\s+", str(phrase or "").strip()):
            cleaned = word.strip()
            if not cleaned:
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(cleaned)}(?!\w)", re.IGNORECASE)
            stripped = pattern.sub(" ", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def _has_turn_free_terms(
    payload: "ResolveReferenceRequest", result: dict[str, Any], query_text: str
) -> list[str]:
    """C2 repair: the described set's free-text half, for a HAS turn that sent NO
    explicit `free_terms` of its own (an ordinary caller that DID supply
    `free_terms` keeps using exactly what it sent - see the caller).

    When every product-entity token DID resolve to an actual PRODUCT match
    ("which sorento bidet has cert" - "bidet" matched three real products), the
    set is already described through those matches' own ids - nothing here
    scopes it a second time, so this returns `[]`. Otherwise (no product-entity
    token at all - a bare class word turn - OR at least one that resolved to
    NO product, whatever else it may have matched) the remainder of
    `query_text` (predicate_words already stripped) becomes ONE term, off its own
    WORDS rather than `payload.tokens` - `_token_of` FOLDS a product token's
    separators out ("water tap" -> "watertap"), which is right for code matching
    and wrong for a phrase a human is meant to read back. Reusing
    `_content_words`'s own stopword/short-word/digit filter is what makes "which
    tap has cert" scope to class Tap and "which item has cert" (no class word at
    all) leave the set unscoped rather than reporting "item" as unrecognized.

    R7/AC-1330 (console fix round 2): "resolved" here means resolved to a
    PRODUCT, specifically - a word token that resolved to some OTHER entity type
    only ("sink" hitting nothing but a customer whose code happens to start
    with "SINK") is NOT thereby "already described"; its raw still has to reach
    `filter_specs` or the set goes unscoped ("count every open-incoming
    product", not "count only Kitchen Sinks"). Measured live: "which sink has
    incoming" counted all 620 products across nine classes instead of the one
    that is actually a sink.

    Before this, nothing on the require branch ever populated a HAS turn's
    `free_terms` at all, so `filter_specs` never saw the class word either way and
    "which tap has cert" counted every certified product, unscoped.
    """
    tokens = payload.tokens or []
    allowed = payload.allowed_entity_types or []
    resolved_product_tokens = {
        str((resolution or {}).get("token") or "").strip().lower()
        for resolution in result.get("resolutions") or []
        if isinstance(resolution, dict)
        and any(
            (match or {}).get("entity_type") == "product"
            for match in resolution.get("matches") or []
        )
    }
    product_indices = [
        i for i, a in enumerate(allowed) if str(a or "").strip().lower() == "product"
    ]
    if product_indices and all(
        str(tokens[i] if i < len(tokens) else "").strip().lower() in resolved_product_tokens
        for i in product_indices
    ):
        return []

    from app.services.product_spec_search import _content_words

    # Fix round, 11 Sep: `predicate_words` only strips its OWN entries whole-word,
    # and the parser sometimes normalises the attachment_type entity's raw to a
    # canonical word ("certificate"/"Certification") that never matches the
    # literal word the customer typed ("cert") - so `predicate_words` alone can
    # leave "cert" sitting in the remainder ("which tap has cert" -> "tap cert").
    # `_CERT_WORD_RE` is the SAME cert-word test `derive_require`/`derive_routing`
    # already use (a local copy, see its own docstring), so every cert-shaped
    # word (and a bare scheme word like "span") is dropped here too, not just
    # the ones the parser happened to echo back.
    words = [w for w in _content_words(query_text) if not _CERT_WORD_RE.search(w)]
    return [" ".join(words)] if words else []


def _result_has_zero_matches(result: dict[str, Any]) -> bool:
    """True when neither OR-mode nor AND-mode produced any match."""
    if "intersection" in result:
        return not result.get("intersection")
    for tr in result.get("resolutions", []) or []:
        if tr.get("matches"):
            return False
    return True


def _every_caller_token_resolved(result: dict[str, Any]) -> bool:
    """D13 (owner turn, 8 Sep 2026, "CB6622-PP?"): every token the caller supplied
    already resolved COMPLETELY, so the spec fallback - built for a word that did NOT -
    must never run over it. AND mode: `intersection` is non-empty and EVERY row in it
    is `match_tier == "exact"`. AND-mode's own PRODUCT probe stamps every row
    `match_tier="and"` (there is no exact tier on that path - see `_and_probe_product`),
    so for a product intersection this stays a backstop and the CB6622-PP repro is
    actually closed by `token_word_coverage_for_rows`'s own dash-normalization fix
    (below). It is NOT idle in general: `_and_probe_warehouse` delegates to
    `_probe_warehouse`, whose rows carry `match_tier="exact"` (8 Sep 2026,
    chatbot-warehouse-entity-and-last-in), so a warehouse-only intersection does reach
    this branch - which is right, an exact warehouse code is a complete answer to its own
    token. A max-coverage row that merely CONTAINS a code word
    (`prefix`/`substring`) is a PARTIAL answer and must stay False
    (SA-P1, "wall hung basin" matching a code that only contains "wall hung"). OR mode:
    every token's own resolution carries at least one match.
    """
    if "intersection" in result:
        rows = result.get("intersection") or []
        return bool(rows) and all((r or {}).get("match_tier") == "exact" for r in rows)
    resolutions = result.get("resolutions")
    if not isinstance(resolutions, list) or not resolutions:
        return False
    return all(bool(tr.get("matches")) for tr in resolutions if isinstance(tr, dict))


def _product_words_unanswered(result: dict[str, Any]) -> bool:
    """AND-shaped result whose returned PRODUCT rows do not, between them,
    contain every word the customer used.

    This catalog writes description words INTO product codes
    (SRTWB7104-WALL HUNG), so max-coverage AND-mode collects partial code
    matches for exactly the phrases spec search exists to answer - "wall hung
    basin" finds 13 codes containing "wall hung" and the zero-match gate never
    fires. Partially matching a code is not answering the description.

    Reads the `token_coverage` the AND exit already computed. Product rows
    only: whether a promotion whose description covered the words answered the
    turn depends on what was asked, and that is `_no_product_row_answered`'s
    rule, not this one's. A missing/unscored coverage block stays False - this
    widens the fallback gate, and a widening must never fire on absence of
    evidence.
    """
    for entry in result.get("token_coverage") or []:
        for claim in (entry or {}).get("coverage") or []:
            if claim.get("entity_type") == "product" and claim.get("unmatched_words"):
                return True
    return False


def _has_unresolved_tokens(result: dict[str, Any]) -> bool:
    """OR-shaped result in which at least one token found nothing.

    A token that matched nothing is unanswered by definition, and the whole point
    of spec search is to answer the words a code probe cannot. Before this,
    "Sorento" prefix-matching four stale codes was enough to declare the turn
    answered, and "double bowl kitchen sink" beside it never reached the ranker
    (live turn 12303509). The relevance floor stays the counterweight against
    nonsense: opening the path is not the same as offering something.
    """
    resolutions = result.get("resolutions")
    if not isinstance(resolutions, list):
        return False
    return any(not (tr.get("matches") or []) for tr in resolutions)


# The parser's domains whose answer is a product row. n8n sends the parser's
# `domain_hint` as `domain`: `master_products` and `product_attachment` already
# canonicalize to `product`; `inventory` is the stock domain and has no alias.
_PRODUCT_QUESTION_DOMAINS: frozenset[str] = frozenset({"product", "inventory"})
# What counts as a product row: the resolver's own product domain (see
# `_expand_entity_types`, which reaches the set probe from the `product` hint).
_PRODUCT_ROW_TYPES: frozenset[str] = frozenset({"product", "product_set"})


def _no_product_row_answered(result: dict[str, Any], domain_hint: str | None) -> bool:
    """A product question that only non-product rows answered is still open.

    Exec 14061515: "any water closet s trap 250mm got stock?" - the one token
    matched eleven promotions whose description contains the words, plus an
    attachment type, and no product. Every other gate counted that as answered,
    so spec search never ran and the customer was told there was no inventory
    "for promotion water closet s trap 250mm". Whether the sentence reached the
    ranker depended on whether the parser had happened to emit a second,
    product-hinted copy of it.

    The rule: on a product question (the parser's `inventory`, `master_products`
    or `product_attachment` domain, sent as `domain`), a token is answered only
    by a product row. A promotion is the answer to a promotion question, and that
    path is untouched, as is a request that named no domain: the gate decides on
    what the caller sent, never on a guess. The relevance floor stays the
    counterweight: opening the path is not the same as offering something.
    """
    if _canonical_entity_type(domain_hint or "") not in _PRODUCT_QUESTION_DOMAINS:
        return False
    if "intersection" in result:
        shown = [result.get("intersection") or []]
    else:
        resolutions = result.get("resolutions")
        if not isinstance(resolutions, list):
            return False
        shown = [tr.get("matches") or [] for tr in resolutions]
    return any(
        not any(m.get("entity_type") in _PRODUCT_ROW_TYPES for m in matches)
        for matches in shown
    )


def _suppress_brand_prefix_junk(
    db: Session, result: dict[str, Any], brands: list[str] | None = None
) -> None:
    """A brand word is answered as a brand, not as whatever codes start with it.

    "sorento" prefix-matches SORENTOBAG and SORENTO188 ("NOT USE THIS CODE"), and
    those rows are what the customer was shown as the answer to a kitchen sink
    question. Once the ranker HAS answered, a code that merely CONTAINS the brand
    word is catalogue noise; an exact full code is still a code and stays, as does
    every non-product match (the brand entity itself among them).

    Only ever called with spec candidates in hand: junk beats silence, so nothing
    is removed on a turn where the ranker found nothing to put in its place.
    """
    resolutions = result.get("resolutions")
    if not isinstance(resolutions, list) or not resolutions:
        return
    tokens = {(tr.get("token") or "").strip().lower() for tr in resolutions}
    tokens.discard("")
    if not tokens:
        return

    # The caller may already hold the brand list for this request; only fetch it
    # when it does not (every other caller stays untouched).
    if brands is None:
        brand_tokens = {
            str(name).strip().lower()
            for (name,) in db.query(Brand.brand_name)
            .filter(func.lower(Brand.brand_name).in_(sorted(tokens)))
            .all()
        }
    else:
        brand_tokens = {str(name).strip().lower() for name in brands} & tokens
    if not brand_tokens:
        return

    unresolved = list(result.get("unresolved_tokens") or [])
    ambiguous = list(result.get("ambiguous_tokens") or [])
    for tr in resolutions:
        token = (tr.get("token") or "").strip().lower()
        if token not in brand_tokens:
            continue
        matches = tr.get("matches") or []
        kept = [
            m
            for m in matches
            if m.get("entity_type") != "product"
            or str(m.get("canonical_code") or "").strip().lower() == token
        ]
        if len(kept) == len(matches):
            continue
        tr["matches"] = kept
        tr["resolved"] = len(kept) == 1
        tr["ambiguous"] = len(kept) > 1
        if not tr["ambiguous"] and tr.get("token") in ambiguous:
            ambiguous = [t for t in ambiguous if t != tr.get("token")]
        if not kept and tr.get("token") not in unresolved:
            unresolved.append(tr.get("token"))
    result["unresolved_tokens"] = unresolved
    if "ambiguous_tokens" in result:
        result["ambiguous_tokens"] = ambiguous


def _collect_match_types(result: dict[str, Any]) -> list[str]:
    """Distinct `entity_type` values across resolutions / intersection."""
    types: set[str] = set()
    for tr in result.get("resolutions", []) or []:
        for m in tr.get("matches", []) or []:
            t = m.get("entity_type")
            if t:
                types.add(t)
    for m in result.get("intersection", []) or []:
        t = m.get("entity_type")
        if t:
            types.add(t)
    return sorted(types)


# One memo across the primary pass and the all-types fallback below: a token the primary
# pass could not resolve is otherwise probed and embedded all over again (CHAT-SLOW-MISS).
@_with_resolve_memo
def _resolve_input(
    db: Session,
    query: str,
    tokens: list[str] | None,
    match_mode: str = "or",
    allowed_entity_types: list[str] | None = None,
    access_levels: list[str] | None = None,
    fallback_to_all_types: bool = False,
    domain_hint: str | None = None,
    entity_pins: dict[str, str] | None = None,
    limit: int | None = None,
    raw_tokens: list[str] | None = None,
    regions: frozenset[str] | None = None,
):
    mode = (match_mode or "or").strip().lower()
    if mode not in _ALLOWED_MATCH_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"match_mode must be one of {sorted(_ALLOWED_MATCH_MODES)}",
        )
    # AND-mode intersection has no per-token view to narrow, so a pin there is
    # silently meaningless - and worse, a zero-intersection AND request retries
    # under `force_mode="or"` further down, where the pin would suddenly start
    # applying. Reject up front rather than let that surprise happen.
    if mode == "and" and entity_pins:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="entity_pins is not supported with match_mode='and'",
        )

    # Treat literal strings "null" / "none" / "undefined" as empty so callers
    # that JSON-encode a missing query as the string "null" don't trip the
    # "no input" path with a deceptively non-empty value.
    _q_norm = (query or "").strip()
    if _q_norm.lower() in {"null", "none", "undefined"}:
        _q_norm = ""

    # Domain-hint dispatch - entity-type takes precedence over AttachmentType
    # because labels collide (e.g. AttachmentType code='promotion' for
    # promotion-attached files vs `promotion` entity-type).
    #   1. Entity-type label (`order`, `product`, `promotion`, `customer`,
    #      `brand`, `category`, ...):
    #       - When `allowed_entity_types` is empty → merge hint as sole scope.
    #       - When `allowed_entity_types` is set → hint is CONTEXT ONLY (no
    #           additive expansion). Caller's whitelist stays authoritative;
    #           hint only informs brand/category expansion overrides via
    #           `_DOMAIN_HINT_EXPANSIONS` inside the resolver.
    #   2. Otherwise try AttachmentType match (`catalogue`, `brochure`, ...).
    #      Short-circuits to attachment-scoped ILIKE search.
    if domain_hint and domain_hint.strip():
        hint_clean = domain_hint.strip()
        hint_canon = _canonical_entity_type(hint_clean)
        if hint_canon in _RESOLVER_ENTITY_TYPES:
            if not (allowed_entity_types or []):
                allowed_entity_types = [hint_clean]
            # else: keep caller's whitelist, hint flows through as context
            # via `_resolve_input` → `_run` → resolver's `domain_hint` kwarg.
        else:
            type_row = _resolve_attachment_type_for_hint(db, hint_clean)
            if type_row is not None:
                hint_tokens = [t for t in (tokens or []) if t]
                return _apply_limit(
                    _resolve_with_domain_hint(db, hint_clean, hint_tokens, _q_norm),
                    limit,
                )
            # Unknown hint - same "context-only when whitelist set" rule.
            if not (allowed_entity_types or []):
                allowed_entity_types = [hint_clean]

    # Local return-wrapper so every exit path gets `limit` applied
    # consistently. Skip the wrap for raw empty-shape payloads (they have no
    # rows; the cap is a no-op there anyway).
    def _ret(result: dict[str, Any]) -> dict[str, Any]:
        return _apply_limit(result, limit)

    if tokens:
        filtered = [t for t in tokens if t]
    else:
        filtered = []

    # Empty-in → empty-out. Callers that send {tokens:[], query:""} (or "null")
    # get an empty ResolutionResult-shaped payload instead of a 400; matches the
    # "nothing resolved, nothing unresolved" semantic the agent expects.
    if not filtered and not _q_norm:
        return {
            "tokens": [],
            "elapsed_ms": 0.0,
            "resolutions": [],
            "unresolved_tokens": [],
            "ambiguous_tokens": [],
        }

    if filtered:
        # entity_pins keys refer to the ORIGINAL token the caller sent (what
        # they can see in their own request), not the stripped form the
        # resolver actually matches against - so remap BEFORE stripping
        # destroys the original spelling. A key with no matching original
        # token is left untouched; it will bind to nothing below and
        # `_apply_entity_pins` rejects it rather than silently dropping it.
        if entity_pins:
            _orig_norm_to_stripped = {
                (t or "").strip().lower(): _strip_entity_stopwords(t) for t in filtered
            }
            entity_pins = {
                _orig_norm_to_stripped.get((key or "").strip().lower(), key): value
                for key, value in entity_pins.items()
            }
        # Strip entity-type noise words ("order 202605-2651" → "202605-2651")
        # so callers can pass conversational phrasing verbatim. Alignment with
        # positional `allowed_entity_types` is preserved (in-place clean).
        filtered = [_strip_entity_stopwords(t) for t in filtered]
        prepared_tokens: list[str] = filtered
        input_for_or: list[str] | str = filtered
    else:
        # AND-mode is per-row intersection, undefined without explicit tokens.
        # Silently degrade to empty result so callers that always send
        # `match_mode='and'` regardless of token shape don't hit a 400 - they
        # get the "nothing matched" payload the agent already knows how to
        # handle. Whitespace splitting a free-text `query` for AND-mode would
        # over-tokenize compound entity names ("sorento latest promo office
        # use" → 5 tokens, intersection guaranteed empty) - not worth doing.
        if mode == "and":
            return {
                "tokens": [],
                "elapsed_ms": 0.0,
                "resolutions": [],
                "unresolved_tokens": [],
                "ambiguous_tokens": [],
            }
        prepared_tokens = []
        input_for_or = _q_norm
        # Pure-alpha entity types (e.g. attachment_type) bypass the code-token
        # regex used inside `resolve_references`. If the caller hinted such a
        # type but did NOT pass `tokens`, synthesize tokens from the query
        # whitespace split so the probes receive candidates.
        if allowed_entity_types:
            wanted = {(t or "").strip().lower() for t in allowed_entity_types if (t or "").strip()}
            if wanted & _ALPHA_TOKEN_TYPES:
                alpha_tokens = _synthesize_alpha_tokens(_q_norm)
                if alpha_tokens:
                    prepared_tokens = alpha_tokens
                    input_for_or = alpha_tokens

    def _run(
        allowed: list[str] | None,
        cross_type_expand: bool = False,
        force_mode: str | None = None,
        tokens_override: list[str] | None = None,
    ) -> dict[str, Any]:
        effective_mode = (force_mode or mode).strip().lower()
        toks = tokens_override if tokens_override is not None else prepared_tokens
        text_input = tokens_override if tokens_override is not None else input_for_or
        # `domain_hint` reaches the resolver so brand / category expansions are
        # scoped to the caller's domain (e.g. domain_hint="order" → category
        # maps to {customer, customer_order, transporter} instead of promotion).
        hint = (domain_hint or "").strip() or None
        if effective_mode == "and":
            raw = resolve_references_intersection(
                db,
                toks,
                allowed_entity_types=allowed,
                domain_hint=hint,
            ).as_dict()
        else:
            raw = resolve_references(
                db,
                text_input,
                allowed_entity_types=allowed,
                cross_type_expand=cross_type_expand,
                domain_hint=hint,
                entity_pins=entity_pins,
                # Only the primary (non-overridden) pass carries raw_tokens: a
                # fallback re-run passes `tokens_override` (a different,
                # unresolved-only token list) so raw_tokens no longer aligns
                # positionally with it - see PLAN-resolver-head-code-retry.
                raw_tokens=raw_tokens if tokens_override is None else None,
                regions=regions,
            ).as_dict()
        raw = _apply_promotion_access_levels_filter(db, raw, access_levels)
        # Promotion-domain hint: run the expander. It owns the dispatch:
        # - promo matches found → return promotion entries only
        #     (regardless of whether caller asked for products).
        # - no promo matches → if caller wants products, surface
        #     through-promotion products; else (and access_levels present)
        #     fallback to brand-access-scoped products as a rescue path.
        if hint and _canonical_entity_type(hint) == "promotion":
            raw = _expand_products_via_promotions(
                db,
                raw,
                list(toks) if isinstance(toks, list) else [],
                access_levels,
                wants_products=_caller_wants_products(allowed_entity_types),
            )
        return raw

    # ------------------------------------------------------------------
    # Primary pass - caller's mode + whitelist applied as-is.
    # ------------------------------------------------------------------
    result = _run(allowed_entity_types)

    if not (fallback_to_all_types and allowed_entity_types):
        # NOTE: `limit` is not applied on this exit - pre-existing behaviour,
        # deliberately preserved (live callers see uncapped counts today, and
        # capping here would move rows under them). Coverage/strip must still
        # run on every AND-shaped exit.
        return _attach_and_coverage(result)

    # ------------------------------------------------------------------
    # Per-token fallback (only for tokens unresolved under the whitelist).
    # Tokens that DID resolve under the whitelist stay untouched - caller's
    # hint is respected and we never expand "technical drawing" into
    # `attachment` matches when they explicitly asked for `attachment_type`.
    #
    # AND-mode shape has no per-token visibility (only `intersection`). When
    # AND failed entirely, degrade to OR-mode-under-whitelist so we can reason
    # per-token. AND that succeeded is returned untouched.
    # ------------------------------------------------------------------
    fallback_match_mode_override: str | None = None
    fallback_reason: str | None = None

    if mode == "and":
        if not _result_has_zero_matches(result):
            # Coverage LAST: it has to describe the post-limit rows, and the
            # limit has to know which types it cut so their claims are
            # flagged incomplete.
            return _attach_and_coverage(_apply_limit_marking_truncation(result, limit))
        result = _run(allowed_entity_types, force_mode="or")
        fallback_match_mode_override = "or"
        fallback_reason = (
            "AND-mode produced zero intersection; switched to OR-mode under "
            "the whitelist so per-token fallback can apply only to unresolved tokens."
        )

    unresolved = [
        r["token"]
        for r in result.get("resolutions", []) or []
        if not (r.get("matches") or [])
    ]
    if not unresolved:
        # Tag and return - at least we've already noted the AND→OR switch.
        if fallback_match_mode_override:
            result["fallback_match_mode"] = fallback_match_mode_override
            result["fallback_reason"] = fallback_reason
        return _ret(result)

    # Cross-type fallback for tokens that didn't resolve under the whitelist.
    # The promotion-domain product expansion (with brand-access gating) already
    # handles the "no promo found → suggest brand-scoped products" path, so
    # this branch fires for non-promotion hints or callers that didn't ask for
    # promotion-domain at all.
    #
    # Raw `attachment` (file) entities are EXCLUDED from this expansion unless the
    # caller explicitly whitelisted `attachment`. A caller resolving product /
    # attachment_type wants a product + a type to query attachments BY downstream;
    # a raw attachment-file row can't be used as a product_id and just pollutes - 
    # e.g. "WC 8609" (no product hit) would otherwise resolve to a photo file
    # `MWC8609-RL.jpg` whose name contains "8609", drowning the real intent. Files
    # stay reachable for callers that ask for them (whitelist `attachment`, or the
    # resource_attachment domain).
    caller_types = {(t or "").strip().lower() for t in (allowed_entity_types or [])}
    fb_allowed = (
        None
        if "attachment" in caller_types
        else sorted(_RESOLVER_ENTITY_TYPES - {"attachment"})
    )
    # No `entity_pins` here: a pinned token either resolved in the primary
    # pass above or the whole request already raised, so `unresolved` never
    # contains a pinned token - threading the dict through would only make
    # `_apply_entity_pins` see pins with nothing in THIS call's resolutions to
    # bind to and reject the request for keys the primary pass already used.
    fb_raw = resolve_references(
        db,
        unresolved,
        allowed_entity_types=fb_allowed,
        cross_type_expand=True,
        regions=regions,
    ).as_dict()
    fb = _apply_promotion_access_levels_filter(db, fb_raw, access_levels)
    # Per-token fallback bypasses `_run`, so the promotion-domain product
    # expansion / brand-access rescue must be applied here too.
    if domain_hint and _canonical_entity_type(domain_hint.strip()) == "promotion":
        fb = _expand_products_via_promotions(
            db,
            fb,
            list(unresolved),
            access_levels,
            wants_products=_caller_wants_products(allowed_entity_types),
        )
    fb_by_token: dict[str, dict[str, Any]] = {
        r["token"]: r for r in fb.get("resolutions", []) or []
    }

    merged: list[dict[str, Any]] = []
    found_any_fallback = False
    for r in result.get("resolutions", []) or []:
        tok = r.get("token")
        has_matches = bool(r.get("matches") or [])
        fb_entry = fb_by_token.get(tok) if tok else None
        if not has_matches and fb_entry and (fb_entry.get("matches") or []):
            merged.append(fb_entry)
            found_any_fallback = True
        elif (
            not has_matches
            and fb_entry
            and not (r.get("alternatives") or [])
            and (fb_entry.get("alternatives") or [])
        ):
            # AC-1703 (reviewer MB-3, 20 Sep 2026): a token that resolved NOWHERE keeps
            # the neighbours the cross-type pass found for it. The whitelist decides
            # which types a token is scanned against, so it decides the trigram
            # neighbours too - and the whitelist is built from the PARSER's hint word,
            # which is a guess about the same typed code ("SRTWT7202-new" came back
            # hinted `product` on one live run and `inbound_shipment` on the next).
            # Under the wrong hint the fallback pass below already finds the real
            # product neighbours (measured: the same two `trgm` rows at 0.76 either
            # way) and this merge threw them away, because it only carried an entry
            # that had MATCHES - so the chatbot answered "I don't know 'new' as a
            # product type" instead of 'Did you mean ...-BL or ...-GM?'. Rescuing the
            # hint is what this whole fallback block is for; a neighbour is not a
            # match, so nothing here resolves that did not resolve before, and the
            # caller's own allow-list still filters what it prints.
            merged.append({**r, "alternatives": list(fb_entry.get("alternatives") or [])})
        else:
            merged.append(r)
    result["resolutions"] = merged
    result["unresolved_tokens"] = [
        m["token"] for m in merged if not (m.get("matches") or [])
    ]

    if found_any_fallback:
        result["fallback_applied"] = True
        result["fallback_requested_types"] = list(allowed_entity_types)
        result["fallback_types_found"] = _collect_match_types(result)
        result["fallback_per_token"] = True
        if fallback_match_mode_override:
            result["fallback_match_mode"] = fallback_match_mode_override
            result["fallback_reason"] = fallback_reason
    elif fallback_match_mode_override:
        # AND→OR switch happened but nothing new came from the per-token fallback.
        result["fallback_match_mode"] = fallback_match_mode_override
        result["fallback_reason"] = fallback_reason

    return _ret(result)


@router.get("/resolve")
def resolve_reference(
    query: str = Query(..., description="Free-text query or a single code to resolve."),
    tokens: list[str] | None = Query(
        None,
        description=(
            "Optional pre-extracted tokens. If provided, the resolver skips regex extraction and "
            "looks up each token verbatim."
        ),
    ),
    match_mode: str = Query(
        "or",
        description=(
            "'or' (default) returns per-token candidates. 'and' returns cross-token intersection "
            " - rows matching every token. AND-mode requires `tokens` (cannot use `query`)."
        ),
    ),
    allowed_entity_types: list[str] | None = Query(
        None,
        description=(
            "Entity-type whitelist. Always a global set filter - every token is "
            "resolved against EVERY supplied type (cross product). Positional 1:1 "
            "pairing is not auto-triggered on equal-length lists; issue one resolve "
            "call per (token, type) pair if you need it. Canonical types include "
            "`attachment_type` (resolves doc-class labels like 'catalogue', "
            "'brochure', 'spec sheet' to AttachmentType UUIDs)."
        ),
    ),
    access_levels: list[str] | None = Query(
        None,
        description=(
            "Post-filter for promotion AND attachment matches. Array of "
            "contact-access-type NAMES (translated to codes via "
            "contact_access_types.name and intersected with the entity's "
            "`access_levels` JSONB column). Matches survive only on intersection; "
            "non-promotion / non-attachment matches untouched. Empty / omitted → no-op."
        ),
    ),
    fallback_to_all_types: bool = Query(
        False,
        description=(
            "When `allowed_entity_types` is set and the first pass returns zero "
            "matches, retry without the whitelist. Response is tagged with "
            "`fallback_applied: true` + `fallback_types_found`."
        ),
    ),
    domain_hint: str | None = Query(
        None,
        description=(
            "Scope resolution to attachments of one AttachmentType (e.g. "
            "'catalogue'). Short-circuits the normal probes and returns "
            "`entity_type='attachment'` rows whose UUIDs feed domain-specific "
            "tools like `crm_resource_attachments_catalogue`. Alias: `domain`."
        ),
    ),
    domain: str | None = Query(None, description="Alias for `domain_hint`."),
    limit: int | None = Query(
        None,
        ge=1,
        le=200,
        description=(
            "Cap returned matches. Applied per-token (each resolution's "
            "`matches` truncated to `limit`) AND to `intersection` in AND-mode. "
            "Omit / null = no cap."
        ),
    ),
    current_user: dict = Depends(get_external_api_user),
    db: Session = Depends(get_db),
):
    """Resolve free-text entity references (product codes, order numbers, shipment numbers, ...)
    to their canonical entity types.

    OR mode (default): per-token, returns canonical UUIDs + display payload + ambiguity signals.
    AND mode: cross-token intersection across each entity's concatenated searchable columns.
    Entity pinning (`entity_pins`) is POST-only - see `ResolveReferenceRequest`; this GET variant
    has no equivalent parameter.
    """
    return _stamp_brand_on_products(
        db,
        _resolve_input(
            db,
            query,
            tokens,
            match_mode=match_mode,
            allowed_entity_types=allowed_entity_types,
            access_levels=access_levels,
            fallback_to_all_types=fallback_to_all_types,
            domain_hint=domain_hint or domain,
            limit=limit,
        ),
    )


# A measurement, never a code: a number carrying a unit. This catalogue's own
# codes look exactly like "B2155", so the code test below cannot also demand two
# letters - and once it does not, "2mm" and "750MM" would read as codes and be
# reported as products we could not find. `L750 x W165 x H247mm` is the flyer's
# own notation for a size and `_labelled_dimensions` binds it as one, so a single
# L/W/H in front of a number is a measurement too.
_MEASUREMENT_SHAPED_RE = re.compile(
    r"^(?:[LWH])?\d+(?:\.\d+)?\s*(?:mm|cm|m|kg|g|l|ml|inch|inches|in|\"|”)?$",
    re.IGNORECASE,
)


def _searchable_words(candidate: dict[str, Any]) -> set[str]:
    """Every word a shown candidate can be said to have answered.

    Its own spec VALUES, the sentence it renders as, and its class - the three
    things a customer reads off the row. A word in here is a word this product
    genuinely speaks to; a word absent from every shown row was not answered by
    showing them.
    """
    words: set[str] = set()

    def absorb(text: Any) -> None:
        for word in re.split(r"[^a-z0-9]+", str(text or "").lower()):
            if word:
                words.add(word)

    absorb(candidate.get("summary"))
    absorb(candidate.get("class"))
    for value in (candidate.get("specifications") or {}).values():
        if isinstance(value, (list, tuple, set)):
            for item in value:
                absorb(item)
        else:
            absorb(value)
    return words


def _is_code_shaped(token: str) -> bool:
    """Is this token shaped like a product CODE the catalogue might hold?

    The resolver's own notion, reused rather than re-guessed: `_CODE_RE` is what
    `extract_candidate_tokens` runs to decide a token is worth a code probe, so a
    token it would probe is exactly a token whose absence is worth reporting.
    The local copy that stood here demanded TWO letters, which quietly exempted
    every single-letter code in the catalogue ("B2155", "S7850") - a customer
    naming one we do not stock was told nothing at all.
    """
    word = str(token or "").strip()
    if len(word) < 3:
        return False
    if _MEASUREMENT_SHAPED_RE.match(word):
        return False
    return bool(_CODE_RE.fullmatch(word))


def _spec_attach_token(result: dict[str, Any], tokens: list[str] | None) -> str | None:
    """The explicit token the spec matches belong to: the FIRST caller-supplied token whose
    resolution came back with no matches. None when the caller sent no tokens, or every
    token matched something - the whole-query resolution then stays as it was."""
    if not tokens:
        return None
    sent = {str(t or "").strip().lower() for t in tokens if str(t or "").strip()}
    for res in result.get("resolutions") or []:
        if not isinstance(res, dict):
            continue
        token = str(res.get("token") or "").strip()
        if token.lower() in sent and not res.get("matches"):
            return token
    return None


def _strip_word_token_product_matches(result: dict[str, Any]) -> None:
    """AC-1327/R2 (console fix round 2): once HAS ran, a WORD token's own
    forward product match - a plain substring/prefix/exact/"and"-tier hit on
    the product CODE, unrelated to the described set - must not survive
    alongside the spec_search resolution: only a `match_tier="spec_search"`
    product match may reach the customer. Console finding, 11 Sep: "which tap
    has cert" answered "Found: <200 codes>" plus a picker of two products named
    "...COLD TAP" - the forward substring hit for "tap" sat right beside the
    real 908-qualifying spec_search resolution, and the GATE's own ambiguity
    block (`still_ambiguous`) fires on any token group whose matches are not
    ALL `spec_search` tier.

    Mutates `result["resolutions"]` and `result["intersection"]` in place;
    every NON-product match (attachment_type, brand, customer, ...) is
    untouched - this is a product-only rule.
    """

    def _keep(match: Any) -> bool:
        if not isinstance(match, dict):
            return True
        if match.get("entity_type") != "product":
            return True
        return match.get("match_tier") == "spec_search"

    for resolution in result.get("resolutions") or []:
        if isinstance(resolution, dict) and isinstance(resolution.get("matches"), list):
            resolution["matches"] = [m for m in resolution["matches"] if _keep(m)]
    if isinstance(result.get("intersection"), list):
        result["intersection"] = [m for m in result["intersection"] if _keep(m)]


def _emit_spec_matches(
    result: dict[str, Any],
    candidates: list[dict],
    token: str,
    bound_words: set[str] | None = None,
    attach_to: str | None = None,
) -> None:
    """Emit ranker candidates as ordinary product matches.

    `spec_candidates` alone was a dead end: it is a different shape parked beside
    the result, so every existing consumer - the n8n spine's resolve-entity, and
    get-results behind it - looked at `resolutions[].matches`, found nothing, and
    treated the turn as unresolved. Describing a product well enough to find it
    only matters if the thing that asks can then USE it.

    Same record shape as every other product match, so nothing downstream needs to
    learn a new one, with `match_tier="spec_search"` so a caller that wants to tell
    a described product from a coded one still can. Shaped exactly like a `prefix`
    match, because that is what it IS: a different WAY of matching, not a lesser
    kind of certainty - a description that finds 15 products must read the same as
    a code prefix that finds 15, or the same shortlist takes two different paths
    depending on how it was found. Only `incoming` and `product_attachment` need
    one exact row, and those callers clarify on their own terms, which they can,
    because `match_tier` says how this was matched.
    """
    if not candidates:
        return
    spec_matches = [
        {
            "entity_type": "product",
            "canonical_code": candidate["product_code"],
            "uuid": candidate["product_id"],
            "match_field": "specifications",
            "match_tier": "spec_search",
            # The ranker's score, so a caller can see how well each one answered
            # rather than treating an ordered list as equally good.
            "similarity": candidate["score"],
            "display": {
                "product_name": candidate["summary"],
                "via_token": token,
                "class": candidate.get("class"),
                # What this product IS, as `{key: value}`. Without it the caller
                # can rank rows it cannot describe: "here are five sinks" with no
                # way to say which one is the 1.2mm one the customer asked for.
                # Copied faithfully, `None` included: null means nothing was ever
                # recorded, where `{}` would claim a block that is merely empty.
                "specifications": candidate.get("specifications"),
                "matched_specs": candidate.get("matched_specs", []),
                # Keys a standing house preference added, kept apart from the ones
                # the customer's own words earned - two different sentences.
                "preferred_specs": candidate.get("preferred_specs", []),
                "is_discontinued": candidate.get("is_discontinued", False),
            },
        }
        for candidate in candidates
    ]
    spec_resolution = {
        "token": token or "specifications",
        "resolved": len(spec_matches) == 1,
        "ambiguous": len(spec_matches) > 1,
        "matches": spec_matches,
        # Empty on purpose: `alternatives` is the did-you-mean channel, and
        # these are matches, not near misses. Putting them here would make the
        # spine offer a list where it should be answering.
        "alternatives": [],
    }
    if "intersection" in result:
        # AND mode carries three views of one answer and the spine reads all of
        # them, so updating `intersection` alone would leave `by_entity_type`
        # empty and `empty` true while 15 products sat in `intersection`.
        #
        # In practice this branch is defensive: an AND run that matches nothing
        # is rewritten to OR shape before spec search runs, so the result
        # reaching here normally has `resolutions` and no `intersection`. Kept
        # because the rewrite is a behaviour of the caller's flags, not a law.
        #
        # D13: an EXACT row survives, a PARTIAL one does not. A `match_tier="exact"`
        # row is a complete answer to its own token - two tokens, one coded and
        # found, one descriptive and not, must keep the coded one when spec search
        # runs for the other. A non-exact row (prefix/substring code overlap, or
        # AND-mode's own `match_tier="and"`) is exactly what spec search exists to
        # supersede - "wall hung basin" matching a code that only CONTAINS "wall
        # hung" has not answered the description, and keeping that row beside the
        # real answer would clutter the reply with what `_product_words_unanswered`'s
        # own docstring calls a non-answer. Deduped by uuid so a code the ranker ALSO
        # surfaces is not counted twice. (AND-mode's PRODUCT probe never stamps
        # "exact" - see `_every_caller_token_resolved` - so for a product intersection
        # this protects an OR-mode-shaped one; `_and_probe_warehouse` does stamp exact,
        # via `_probe_warehouse`, and a warehouse row surviving here is correct. The rule
        # is the same either way, not a special case for one shape.)
        existing = [
            m for m in (result.get("intersection") or []) if (m or {}).get("match_tier") == "exact"
        ]
        existing_uuids = {m.get("uuid") for m in existing if isinstance(m, dict)}
        merged = existing + [m for m in spec_matches if m.get("uuid") not in existing_uuids]
        result["intersection"] = merged
        by_type: dict[str, list[dict[str, Any]]] = {}
        for match in merged:
            by_type.setdefault(match["entity_type"], []).append(match)
        result["by_entity_type"] = by_type
        result["empty"] = not merged
        # Coverage was computed inside _resolve_input over rows this branch just
        # REPLACED - recompute over what the route actually sends, or the field
        # describes rows that no longer exist. Spec rows carry no scored text,
        # so this honestly yields no claims.
        _attach_and_coverage(result)
    # `attach_to` (D6): fold the matches into that explicit token's own resolution,
    # keeping its token, instead of appending a whole-query one.
    target = None
    if attach_to:
        target = next(
            (
                r
                for r in (result.get("resolutions") or [])
                if isinstance(r, dict)
                and str(r.get("token") or "").strip().lower() == attach_to.strip().lower()
            ),
            None,
        )
    if target is not None:
        target["matches"] = spec_matches
        target["resolved"] = spec_resolution["resolved"]
        target["ambiguous"] = spec_resolution["ambiguous"]
        target.setdefault("alternatives", [])
    else:
        result.setdefault("resolutions", []).append(spec_resolution)
    # Something was found, so the words it answered are no longer unresolved.
    #
    # "Answered" means the CANDIDATES answered it, word by word. Membership of
    # the searched TERM was not enough: the whole sentence is one term, so every
    # descriptive token in the turn was cleared by any spec row at all - a
    # customer who asked for a sink AND a bathroom mirror got sinks and was told
    # nothing about the mirror, and a company name sitting in the sentence was
    # silently declared found. A token is cleared only when every content word in
    # it either earned a binding for this query, or appears in the text of a
    # product actually being shown.
    #
    # Nothing else is touched: `unresolved_tokens` and `alternatives` are what
    # drive "did you mean", and a match is neither.
    #
    # `_content_words` is the honesty channel's own reading of "which words here
    # could carry product meaning", reused so a token cannot be cleared on words
    # `unrecognized_terms` would never have checked.
    from app.services.product_spec_search import _content_words

    answered_words: set[str] = set(bound_words or set())
    for candidate in candidates:
        answered_words |= _searchable_words(candidate)

    def _answered(candidate_token: str) -> bool:
        words = _content_words(str(candidate_token or ""))
        if not words:
            # Nothing reportable in it (a bare measurement, punctuation, a
            # stopword): there is no claim to keep alive.
            return True
        return all(word in answered_words for word in words)

    # Spec rows answer DESCRIPTIONS. They never vouch for a CODE the catalogue
    # does not contain, so a code-shaped token keeps its place in the footer even
    # when the sentence around it found products: "ZZTKS999 kitchen sink" must
    # still say the code was not found, or the customer reads the sinks as the
    # answer to a code we never had.
    result["unresolved_tokens"] = [
        t
        for t in (result.get("unresolved_tokens") or [])
        if _is_code_shaped(t) or not _answered(t)
    ]


@router.post("/resolve")
def resolve_reference_post(
    payload: ResolveReferenceRequest,
    current_user: dict = Depends(get_external_api_user),
    db: Session = Depends(get_db),
):
    """POST variant for external callers that send JSON body (e.g. n8n HTTP node)."""
    # A code, then words that pick among its variants ("Srtwc7604 p trap price"; owner
    # hand test of rounds 4 to 6 on PR #833, items 2 to 4): the code token becomes the
    # variant codes the words pick, and the description token those words came from is
    # dropped, so the ask resolves as that code's variants and never as a category set.
    from app.services.product_code_family import narrow_code_tokens

    narrowed = narrow_code_tokens(
        db, query=payload.query or "", tokens=payload.tokens, allowed_types=payload.allowed_entity_types
    )
    if narrowed is not None:
        payload = payload.model_copy(update={"tokens": narrowed, "raw_tokens": None})
    # A contact's packing list regions (unresolved = West) bound the shipments it can be
    # shown; no contact_id = no filter.
    regions = None
    if payload.contact_id:
        from app.services.eta_policy import resolve_request_contact, rules_for_contact

        regions = rules_for_contact(
            db, resolve_request_contact(db, payload.contact_id, payload.space_id)
        ).regions
    try:
        result = _resolve_input(
            db,
            payload.query,
            payload.tokens,
            match_mode=payload.match_mode,
            allowed_entity_types=payload.allowed_entity_types,
            access_levels=payload.access_levels,
            fallback_to_all_types=payload.fallback_to_all_types,
            domain_hint=payload.domain_hint,
            entity_pins=payload.entity_pins,
            limit=payload.limit,
            raw_tokens=payload.raw_tokens,
            regions=regions,
        )
    except EntityPinMismatch as exc:
        raise AppException(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=(
                f"entity_pins uuid for token {exc.token!r} does not match any "
                "candidate the resolver found for that token."
            ),
            detail=f"token={exc.token} pinned_uuid={exc.pinned_uuid}",
            code="ENTITY_PIN_MISMATCH",
        ) from exc

    if payload.exact_match:
        _drop_word_neighbours(result)

    # Shape B: a domain predicate over the described set. This is NOT a fallback -
    # "what faucets have certs" is a different question from "find me a faucet",
    # and it runs whenever the parser asked it, whatever the normal probes found -
    # UNLESS a caller token matched a product by code (`_code_matched`: code first,
    # spec search only when no code matched), so the response stays byte-identical to
    # the same request without `require`.
    if payload.require and not _code_matched(result, payload.tokens):
        from app.services.product_predicate_service import (
            recover_certificate_scheme,
            resolve_product_set,
        )
        from app.services.product_spec_search import _content_words
        from app.services.product_spec_understanding import derive_search_inputs

        # C2: the described set's other half besides `product_ids` - class /
        # product_type / brand bindings read off the query, `predicate_words`
        # stripped first so the predicate's own word ("cert", "stock") is never
        # also asked to bind a spec. R13/AC-1332 (third console pass):
        # `allow_model` is always False here, never `payload.understand_phrase`
        # - the MODEL phrase reader is a spec_fallback-only mechanism (2-3
        # seconds, gated there by design); a HAS turn's bindings must be
        # deterministic (registry synonyms, brand names) only. Measured live:
        # "which item has PPS cert" answered "I don't know 'item pps' as a
        # product type" - "item pps" is a model-derived free term the
        # deterministic reader never produces.
        #
        # `free_terms=[]` into the reader on purpose, and its OWN returned free
        # terms are dropped, never merged into `payload.free_terms`:
        # `derive_search_inputs` always echoes the whole phrase back as a free
        # term (its fallback shape, for the ranker's free-text boost), and
        # merging that in fed the raw sentence to `filter_specs`'s honesty
        # check as if the caller had typed it as a described term - reporting
        # "which kitchen sinks have stock" itself as unrecognized. Only the
        # BINDINGS (`specs`) are wanted here; ranking still runs on exactly the
        # free terms the caller sent, unchanged.
        query_text = _strip_predicate_words(payload.query or "", payload.predicate_words)

        # R6 (round 4 on PR #833, "why it match s trap?"): a value the registry does not
        # know ("t trap") is said back with the ones it does; the set is never counted
        # with it dropped or read as its nearest neighbour.
        from app.services.product_spec_search import unknown_spec_values

        unknown = _with_grounded_unknowns(
            unknown_spec_values(db, " ".join([payload.query or "", *(payload.scope_terms or [])])),
            payload.unknown_values,
        )
        if unknown:
            result["predicate"] = {
                "require": payload.require,
                "qualifying_total": 0,
                "truncated": False,
                "unrecognized_terms": [u["said"] for u in unknown],
                "unknown_values": unknown,
            }
            # Fix round 9 on PR #833: the subject without the unknown value, broken down
            # by that value's key, so the reply lists what the set does come in.
            members = _unknown_value_members(db, payload, query_text, unknown, current_user)
            if members:
                result["predicate"]["members"] = members
            return _stamp_brand_on_products(db, result)

        # R14/AC-1338 (third console pass): a bare `{"certificate": True}`
        # require whose remainder still holds the scheme word (never split
        # off the attachment_type raw upstream - "which item has PPS cert"
        # never separated "PPS" from "cert") is promoted here, off the SAME
        # remainder `derive_search_inputs` is about to read. The consumed
        # word is dropped from `query_text` BEFORE it reaches either
        # `derive_search_inputs` or `_has_turn_free_terms` (both read this
        # one shared variable), so it never lands in `filter_specs` as an
        # unrecognized set word.
        require, consumed_scheme_words = recover_certificate_scheme(
            db, payload.require, _content_words(query_text)
        )
        if consumed_scheme_words:
            query_text = _strip_predicate_words(query_text, consumed_scheme_words)

        specs, _derived_free_terms, _exclusions, understanding = derive_search_inputs(
            db,
            query_text,
            specs=list(payload.extracted_specs or []),
            free_terms=[],
            allow_model=False,
            user_id=current_user.get("id"),
            log_usage=not payload.dry_run,
        )
        # D3: a brand scopes the WHOLE set - kept OUT of `specs`' union-membership
        # role (a brand binding there would union in every OTHER certified product
        # of that brand, not just the described ones) and applied only as the
        # service's own AND-scope.
        brand_entry = next((e for e in specs if e.get("key") == "brand"), None)
        brand = str(brand_entry["value"]) if brand_entry else None
        specs = [e for e in specs if e.get("key") != "brand"]

        # R27/AC-1351: a phrase the deterministic reader BOUND to a spec key
        # ("s trap" -> trap_type, off "with s trap") is already answered by
        # `specs` - left in `query_text` it reaches `_has_turn_free_terms`
        # below as an extra word ("s trap") that names no class, so the set
        # scope term would carry it and `filter_specs`' honesty check would
        # report it unrecognized even though the request WAS understood.
        # Same pattern as the R14 consumed certificate-scheme word: strip
        # every bound phrase out of the remainder before anything reads it
        # again. Digits are already dropped by `_content_words` downstream,
        # so "250mm" needs no handling here.
        bound_words = [phrase for phrases in understanding.bound_phrases.values() for phrase in phrases]
        if bound_words:
            query_text = _strip_predicate_words(query_text, bound_words)

        # C2 repair: a caller that sent its own `free_terms` keeps them untouched
        # (test_require_returns_the_predicate_block_and_ordinary_matches's own
        # ["kitchen sink"]); the ordinary chatbot lane never sends this key at
        # all, so THIS is the one place a bare class word ("tap", "basin")
        # reaches `filter_specs` for a HAS turn. Passed as `scope_terms`, never
        # merged into `free_terms`: a derived class word scopes the set but must
        # never also drive `search_specs` ranking, which would silently evict a
        # `product_ids`-only match that carries no spec row at all.
        # An explicit `scope_terms` wins: the caller's parser NAMED the class word,
        # which is a better answer than slicing it back out of the raw message - and
        # the only answer at all when the message that armed the turn is not this
        # turn's own text (a pick, a carried subject).
        scope_terms = (
            list(payload.scope_terms)
            if payload.scope_terms
            else (None if payload.free_terms else _has_turn_free_terms(payload, result, query_text))
        )

        # The counted set lists off the QUALIFYING ids themselves, capped at
        # `_SET_ID_CAP` (200) - never the ordinary LOOKUP page size
        # (`payload.limit`, 15), which would cut a 40-product set that fits one
        # message down to 15.
        # Read BEFORE `_emit_spec_matches` adds the qualifying set to the resolutions.
        lookup_ids = _collect_lookup_product_ids(result)
        outcome = resolve_product_set(
            db,
            # R14/AC-1338: the PROMOTED require (a bare `true` recovered a
            # scheme from the remainder above), never `payload.require`
            # unconditionally - `require` is that same dict unchanged when
            # nothing was recovered.
            require=require,
            specs=specs,
            free_terms=payload.free_terms,
            scope_terms=scope_terms,
            limit=max(payload.limit or 0, _SET_ID_CAP),
            product_ids=lookup_ids or None,
            brand=brand,
            # SEC-S1/AC-1334: the promotion leg must see the caller's OWN tier,
            # not only the ordinary entity-resolution filter
            # (`_apply_promotion_access_levels_filter`) further down - a HAS
            # turn's qualifying_total must never count a tier-restricted
            # promotion the contact cannot see.
            access_levels=payload.access_levels,
            # The stock leg counts only the locations the asking contact's own
            # stock visibility policy allows, as the stock tool answers them.
            stock_policy=_stock_policy_for(db, payload) if require.get("stock") else None,
            # Fix round 9 on PR #833 (owner, 28 Sep 2026): no brand named means every
            # brand, never a silent default. R1's weighted default is off here; a set too
            # long for one message is broken down by brand instead (`breakdown`).
            prefer_weighted_brand=False,
            breakdown_over=payload.set_list_max,
        )
        # One nested block, not top-level scalars: n8n item-mutation chains
        # persist top-level keys across nodes. And never inside `by_entity_type`,
        # which n8n renders to customers.
        result["predicate"] = {
            "require": outcome["require"],
            "qualifying_total": outcome["qualifying_total"],
            "truncated": outcome["truncated"],
            "unrecognized_terms": outcome["unrecognized_terms"],
        }
        # D2/AC-1313: present ONLY on a scheme miss - a resolvable scheme carries
        # no such key at all, so a caller reading it never has to tell "no schemes"
        # from "not a scheme question".
        if "schemes_on_file" in outcome:
            result["predicate"]["schemes_on_file"] = outcome["schemes_on_file"]
        # R6/AC-1329: present ONLY on an attachment-LABEL miss ("photo", not a
        # class/product_type word) - the reply clarifies it as a document type,
        # never the product-type sentence below.
        if "attachment_types_on_file" in outcome:
            result["predicate"]["attachment_types_on_file"] = outcome["attachment_types_on_file"]
        # E2/AC-1316: the described set's class label(s), for the set-answer
        # header's noun (`answer.set_noun_for`) - present only when non-empty
        # (AC-1309's own shape-lock test asserts `predicate` carries EXACTLY its
        # four documented keys on a turn with no class to name).
        if outcome.get("class_labels"):
            result["predicate"]["class_labels"] = outcome["class_labels"]
        # R29/AC-1354: present ONLY on a scheme-narrowed certificate leg - a
        # bare leg has no scheme to narrow the files by, same present-only
        # convention as `schemes_on_file`.
        if outcome.get("certificate_ids"):
            result["predicate"]["certificate_ids"] = outcome["certificate_ids"]
        # W2/W1 (owner hand test round 2): what the set was identified as, in plain
        # words, and its brand - present only when there is one to say.
        if outcome.get("description"):
            result["predicate"]["description"] = outcome["description"]
        if outcome.get("brand"):
            result["predicate"]["brand"] = outcome["brand"]
            # The brand word IS placed: it scopes the set. Left in `unresolved_tokens`
            # it closed a Sorento answer with "I could not find sorento."
            brand_key = str(outcome["brand"]).strip().lower()
            result["unresolved_tokens"] = [
                t for t in (result.get("unresolved_tokens") or []) if str(t).strip().lower() != brand_key
            ]
        if outcome["qualifying_total"] and scope_terms:
            # The class words ARE placed too: they described the set the header names.
            # Owner hand test of rounds 4 to 6 on PR #833, items 5 and 7: "close couple wc"
            # and "WALL MOUNTED KITCHEN TAP" were read into the header, then the reply
            # closed with "I could not find close couple wc." A word the reader could
            # not use stays reported (`unrecognized_terms`).
            unread = {str(t).strip().lower() for t in outcome.get("unrecognized_terms") or []}
            placed = {str(t).strip().lower() for t in scope_terms} - unread
            result["unresolved_tokens"] = [
                t for t in (result.get("unresolved_tokens") or []) if str(t).strip().lower() not in placed
            ]
        if outcome.get("row_labels"):
            result["predicate"]["row_labels"] = outcome["row_labels"]
        if outcome.get("other_brands"):
            result["predicate"]["other_brands"] = outcome["other_brands"]
        # R4 (round 4): what a zero set looked for and the count in its other values.
        if outcome.get("near_miss"):
            result["predicate"]["near_miss"] = outcome["near_miss"]
        # Fix round 9: a long set's breakdown by the next attribute, and a zero set's
        # described products without the legs.
        for key in ("breakdown", "members", "set_brands"):
            if outcome.get(key):
                result["predicate"][key] = outcome[key]
        # W4: what a page of this set replays - the bound specs, the brand and the ids
        # LOOKUP matched (the other half of the union) - so the page counts the same set.
        if outcome["qualifying_total"]:
            result["predicate"]["set_key"] = {
                "specs": outcome.get("set_specs") or [],
                "brand": outcome.get("brand"),
                "brand_default": bool(outcome.get("brand_default")),
                "product_ids": lookup_ids[:_SET_ID_CAP],
            }
        _emit_spec_matches(result, outcome["candidates"], payload.query or "")
        # R2 only fires on a genuine HAS answer (qualifying_total > 0): the
        # existing zero-qualifying miss flow names its own candidate codes off
        # these SAME forward matches (F1's pre-existing "Couldn't find a bidet
        # with a certificate. ACC-BIDET, CAB-BIDET, SRT-BIDET" copy) - stripping
        # them there would silence the very codes that message names.
        if outcome["qualifying_total"]:
            _strip_word_token_product_matches(result)
        return _stamp_brand_on_products(db, result)

    # Spec search is a FALLBACK, never a parallel path. It runs only when the caller
    # asked for it AND the normal (code-only) product probes found nothing - or
    # found only PARTIAL code-word overlap (`_product_words_unanswered`): a code
    # that happens to contain "WALL HUNG" has not answered "wall hung basin".
    # Or, on a product question, found no product row at all
    # (`_no_product_row_answered`): a promotion is not stock.
    # The response stays byte-identical for every existing caller and for every
    # request that resolves a code fully. The product probes themselves are
    # untouched: see _and_probe_product's "CODE-ONLY by design" note.
    #
    # D13: `_every_caller_token_resolved` is checked FIRST and short-circuits the
    # whole block - every other signal here answers "is a WORD unanswered", and a
    # query whose every token already resolved exactly has no unanswered word by
    # definition, whatever a coverage claim over punctuation says.
    if (
        payload.spec_fallback
        and not _every_caller_token_resolved(result)
        and (
            _result_has_zero_matches(result)
            or _product_words_unanswered(result)
            or _has_unresolved_tokens(result)
            or _no_product_row_answered(result, payload.domain_hint)
        )
    ):
        import time

        from app.services.product_spec_registry import active_registry
        from app.services.product_spec_search import (
            brand_names,
            search_specs,
            unrecognized_terms,
        )

        # ONE read of each per request. The registry and the brand list are
        # consulted by the binder, the vocabulary and the junk suppressor, and
        # each used to fetch its own copy - four round trips for two tables that
        # cannot change mid-request.
        registry_rows = active_registry(db)
        brands = brand_names(db)

        # R6 (round 4 on PR #833): "any water clost t trap?" listed S trap water closets.
        # A value the registry does not know is said back (`answer.unknown_values_sentence`),
        # never searched for as its nearest neighbour, so no spec candidate is offered.
        from app.services.product_spec_search import unknown_spec_values

        unknown = _with_grounded_unknowns(
            unknown_spec_values(db, payload.query or "", registry_rows=registry_rows),
            payload.unknown_values,
        )
        if unknown:
            result["unknown_spec_values"] = unknown
            return _stamp_brand_on_products(db, result)

        # The sentence is ALWAYS read - through the SAME helper the Product
        # Specifications preview page uses, which is why raw text "just works"
        # there. The word-level read (allow_model=False) is deterministic and
        # free; only the MODEL read costs 2-3 SECONDS on the reply path, so that
        # half stays behind the caller's flag, and `semantic_used` lets the
        # caller tell the customer why the answer is slow instead of leaving
        # them watching nothing.
        #
        # Before this ran unconditionally, a caller sending only `query` left
        # the ranker blind, so n8n rebuilt free_terms from its parser's entities
        # - a lossy hop that dropped "thickness 1.0mm" before the CRM ever saw
        # it (live turn 12303548). Raw text in, CRM derives; explicit
        # extracted_specs/free_terms still win over the derived reading.
        from app.services import product_spec_understanding

        started = time.monotonic()
        specs, free_terms, exclusions, understanding = (
            product_spec_understanding.derive_search_inputs(
                db,
                payload.query,
                specs=list(payload.extracted_specs or []),
                free_terms=list(payload.free_terms or []),
                allow_model=payload.understand_phrase,
                user_id=current_user.get("id"),
                registry_rows=registry_rows,
                # D14: a dry run reads the sentence exactly the same way and simply does
                # not write the usage row for it.
                log_usage=not payload.dry_run,
            )
        )
        semantic_used = bool(understanding and understanding.source == "semantic")
        semantic_ms = (
            int((time.monotonic() - started) * 1000) if understanding is not None else None
        )

        # AC-18 (PLAN-spec-visibility-policy.md "Spec fallback"): a hidden key
        # neither RANKS the catalog (dropped from `specs` before `search_specs`
        # runs) nor PRINTS (stripped from every candidate below) - the second
        # half is needed even though the first already ran, because a
        # candidate's `specifications` is the product's OWN full stored values,
        # independent of what was searched for.
        #
        # B/S2 (security review): the CALLER's `hidden_spec_keys` is trusted
        # input a caller could forget to compute, forget to forward, or send
        # stale - so when `contact_id` is also sent, the route resolves that
        # contact's policy itself (the same `resolve_policy` + `hidden_keys`
        # pair `check_access` uses) and UNIONS it in. The caller-supplied list
        # only ever narrows further; it can never widen past the server's own
        # answer for that contact.
        hidden_spec_keys = {str(k) for k in (payload.hidden_spec_keys or [])}
        if payload.contact_id:
            from app.services.field_access import (
                resolve_contact_with_null_workspace_fallback,
            )
            from app.services.spec_visibility import (
                full_registry_rows,
                hidden_keys as _hidden_keys,
                resolve_policy as _resolve_spec_policy,
            )

            # SF-1 (security re-verify): resolved through the SAME NULL-
            # workspace fallback `check_access` uses, not the bare
            # `resolve_contact_id` `resolve_policy` calls internally - a
            # contact with `workspace_id IS NULL` (16 measured) resolves to
            # nothing through the plain path, so this route would have handed
            # such a contact the closed DEFAULT policy while `check_access`
            # gives them their real one. Once resolved to the internal id,
            # `resolve_policy` re-resolving it is a same-id no-op.
            resolved_contact_id = resolve_contact_with_null_workspace_fallback(
                db, contact_id=payload.contact_id, space_id=payload.space_id
            )
            contact_policy = _resolve_spec_policy(
                db, resolved_contact_id or payload.contact_id, payload.space_id
            )
            registry_keys = {key for key, _label in full_registry_rows(db)}
            hidden_spec_keys |= _hidden_keys(contact_policy, registry_keys)
        if hidden_spec_keys:
            specs = [s for s in specs if s.get("key") not in hidden_spec_keys]

        # Fix round 10 on PR #833 (owner, 28 Sep 2026: "for #833 yeah exact only"): the
        # chatbot (`exact_match`) is listed only products that hold every value asked
        # for, never the nearest by relevance (`search_specs(exact=True)`).
        found = search_specs(db, specs=specs, exclusions=exclusions, free_terms=free_terms, exact=payload.exact_match)
        if hidden_spec_keys:
            from app.services.product_spec_rendering import render_spec_sentence

            for candidate in found.get("candidates") or []:
                spec_values = candidate.get("specifications")
                matched_specs = candidate.get("matched_specs")
                carried_hidden = isinstance(spec_values, dict) and (
                    hidden_spec_keys & spec_values.keys()
                )
                if isinstance(spec_values, dict):
                    filtered_values = {
                        k: v for k, v in spec_values.items() if k not in hidden_spec_keys
                    }
                    candidate["specifications"] = filtered_values
                    # B1 (code review): `summary` is the rendered sentence
                    # (`rendered_text`), built from the SAME values - a hidden
                    # key's own number or word survives inside it otherwise
                    # ("... 1.2 mm thick.") - and `_emit_spec_matches` copies it
                    # straight into `display.product_name`, the one field the
                    # filtering above never reached. Re-rendered from the
                    # FILTERED values through the real renderer (never a regex
                    # edit of the old sentence, which could not tell a hidden
                    # number from any other) - and ONLY for a candidate that
                    # actually carried a hidden key (nit, re-verify): a
                    # candidate with nothing to hide keeps its stored
                    # `rendered_text` byte-identical rather than a
                    # re-derivation that could drift from it in wording.
                    if carried_hidden:
                        # `render_spec_sentence` reads `{key: {"value": ...}}`;
                        # `filtered_values` is already values-only, so each is
                        # re-wrapped one level to match.
                        # The brand rides on `specifications` from the product's own
                        # field (it is not a specification, #1286), so it is handed
                        # to the renderer beside the values rather than inside them.
                        nested = {
                            k: {"value": v}
                            for k, v in filtered_values.items()
                            if k != "brand"
                        }
                        # A None render (nothing left to say) falls back to the
                        # product's own code, never the ORIGINAL summary - an
                        # identifying code is not a leak of what filtering
                        # removed, where the unfiltered sentence would be.
                        candidate["summary"] = render_spec_sentence(
                            nested, brand=filtered_values.get("brand")
                        ) or candidate.get("product_code", "")
                if isinstance(matched_specs, list):
                    candidate["matched_specs"] = [
                        k for k in matched_specs if k not in hidden_spec_keys
                    ]
        result["spec_candidates"] = found["candidates"]
        result["floor_missed"] = found["floor_missed"]

        # A brand token's code-prefix junk stops headlining, but only now that the
        # ranker has something to show instead.
        if found["candidates"]:
            _suppress_brand_prefix_junk(db, result, brands=brands)

        # Words the shown products ANSWERED through a binding - the customer's
        # own words where they were heard (`bound_phrases`), plus what they
        # resolved to. Restricted to keys a shown row actually matched: binding
        # "bathroom mirror" to `product_type=mirror` and then showing five sinks
        # has not answered the mirror, and clearing it from the footer would say
        # it had.
        satisfied = {
            key
            for candidate in found["candidates"]
            for key in candidate.get("matched_specs") or []
        }
        bound_words: set[str] = set()
        parts = [
            str(phrase)
            for key, phrases in (understanding.bound_phrases if understanding else {}).items()
            if key in satisfied
            for phrase in phrases
        ]
        for entry in found["asked_for"]:
            if str(entry.get("key")) in satisfied:
                parts.extend([str(entry.get("key") or ""), str(entry.get("value") or "")])
        for part in parts:
            for word in re.split(r"[^a-z0-9]+", part.lower()):
                if word:
                    bound_words.add(word)
        # AND emit them as ordinary product matches (see _emit_spec_matches).
        # D6 (owner console pass, 8 Sep 2026, turn 333c37cf "Ibwc7605 image"): with EXPLICIT
        # `tokens`, the spec matches attach to the unresolved product token's own
        # resolution rather than to a third, whole-query token - the lane's did-you-mean
        # rendered that phrase token as a SECOND miss group ("Ibwc7605 image" beside
        # "Ibwc7605"). Measured with both "image" and "photo": the attachment word is not
        # the trigger, `spec_fallback` is. A caller that sends no `tokens` keeps today's
        # whole-query resolution, byte for byte.
        _emit_spec_matches(
            result,
            found["candidates"],
            payload.query or "",
            bound_words=bound_words,
            attach_to=_spec_attach_token(result, payload.tokens),
        )
        # What the customer asked for that nothing offered can satisfy. The caller says
        # "no Cabana one, here are Sorento" rather than silently substituting.
        result["spec_unmet"] = found["unmet"]
        # What the sentence was READ as asking for, as the ranker resolved it and
        # AFTER free terms bound. The caller can then say "you asked for 1.2mm"
        # without re-parsing the customer's words with a second, different reader.
        # House preferences are absent by construction: they are not asks.
        result["spec_asked"] = found["asked_for"]
        # The other half of the same honesty, and a different sentence: `spec_unmet`
        # is a KNOWN key the catalogue is silent on ("thickness isn't recorded for
        # these"), this is a word that bound to nothing at all ("I don't know what
        # 'flurbish' means"). Same field name and semantics as shape B's
        # `predicate.unrecognized_terms`, so a caller learns one vocabulary.
        #
        # It speaks ONLY for a turn that was describing a product - candidates
        # came back, or something in the sentence bound. "Quotation for Encik
        # Baharudin" is not a product description at all, and answering it with
        # "I don't know what 'baharudin' means" is the CRM mistaking a person's
        # name for a spec. The field stays on the wire either way: a caller
        # reading it must never have to tell absent from empty.
        descriptive = bool(found["candidates"]) or bool(specs)
        result["unrecognized_terms"] = (
            unrecognized_terms(
                db,
                query=payload.query,
                free_terms=payload.free_terms,
                registry_rows=registry_rows,
                brands=brands,
            )
            if descriptive
            else []
        )
        result["semantic_used"] = semantic_used
        if semantic_ms is not None:
            result["semantic_ms"] = semantic_ms

    return _stamp_brand_on_products(db, result)
