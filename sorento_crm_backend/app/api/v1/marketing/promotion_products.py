"""Promotion products API routes."""
import re
import uuid

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional, Tuple
from app.database import get_db
from app.dependencies import require_any_permission_with_api_key

from app.services.marketing_service import PromotionProductService, _resolve_promotion_id_for_filter
from app.services.uuid_list_param import parse_uuid_list
from app.schemas.marketing import (
    PromotionProductCreate,
    PromotionProductUpdate,
    PromotionProductResponse,
    PromotionServingListResponse,
)
from app.schemas.common import MAX_PAGE_LIMIT
from app.services.error_handler import handle_internal_error

router = APIRouter()


# Who reads promotion product lines (LIST-API-PERMS): the Promotion Products page, and the
# product detail's Promotions tab (`getPromotionsByProductId`, `ProductPromotionsTab.tsx`).
PROMOTION_PRODUCT_READERS = ["marketing.promotion_products.view", "master_data.products.view"]

# Mistaken MCP usage: query="promotion_id='<uuid>'" or query="<uuid>" - treat as promotion filter when promotion_id is omitted.
_PROMOTION_ID_ASSIGN_RE = re.compile(
    r"^\s*promotion_id\s*=\s*(?:'|\")?([0-9a-fA-F-]{36})(?:'|\")?\s*$",
    re.IGNORECASE,
)


def _implicit_promotion_and_text_query(q: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """If *q* is a bare UUID or looks like promotion_id=<uuid>, return (uuid_str, None); else (None, q) for text search."""
    if q is None:
        return None, None
    s = str(q).strip()
    if not s:
        return None, None
    m = _PROMOTION_ID_ASSIGN_RE.match(s)
    if m:
        return m.group(1), None
    try:
        return str(uuid.UUID(s)), None
    except ValueError:
        return None, q


# Auto-promote dimension/price tokens embedded in `query` into structured filters.
# Robust to LLM mis-use (e.g. query="basin 365 width" → width_min/max=365 ± tol, query="basin").
#
# Patterns matched (case-insensitive):
# - "L600" / "W365" / "H140"            → length/width/height = N
# - "600mm wide" / "365 width"           → width = N
# - "365 mm" / "365mm"                   → unspecified-axis (any_dimension) = N
# - "L 600-650" / "width 360..370"       → range form
# - "price 100-200" / "rm 150"           → price_min/max
_AXIS_WORDS = {
    "length": ("length", "long", "l"),
    "width": ("width", "wide", "w"),
    "height": ("height", "tall", "high", "h"),
}
_AXIS_WORD_TO_KEY = {w: k for k, ws in _AXIS_WORDS.items() for w in ws}

# Single-letter axis directly attached to digits: L600, W365, H140
_AXIS_LETTER_RE = re.compile(r"(?i)(?<![a-z])([lwh])\s*(\d{2,5})(?:\s*[- - ]\s*(\d{2,5}))?(?:\s*mm)?\b")
# Number ± axis word: "365 width", "365 mm wide", "width 365", "600mm long"
_NUM_AXIS_RE = re.compile(
    r"(?i)(?:(\d{2,5})(?:\s*[- - ]\s*(\d{2,5}))?\s*(?:mm)?\s+(length|long|width|wide|height|tall|high)"
    r"|(length|long|width|wide|height|tall|high)\s+(\d{2,5})(?:\s*[- - ]\s*(\d{2,5}))?(?:\s*mm)?)"
)
# Plain number with mm and no axis: "365mm", "365 mm"
_NUM_MM_RE = re.compile(r"(?i)\b(\d{2,5})(?:\s*[- - ]\s*(\d{2,5}))?\s*mm\b")
# Price: "rm150", "MYR 200", "price 100-200"
_PRICE_RE = re.compile(r"(?i)(?:rm|myr|price|cost)\s*(\d{1,7})(?:\s*[- - ]\s*(\d{1,7}))?")


def _apply_axis_range(target: dict, axis: str, lo: float, hi: float) -> None:
    min_key = f"{axis}_min"
    max_key = f"{axis}_max"
    # User-supplied param wins; only fill when missing.
    if target.get(min_key) is None:
        target[min_key] = lo
    if target.get(max_key) is None:
        target[max_key] = hi


def _parse_query_dimensions(
    query: Optional[str],
    existing: dict,
    tolerance_mm: int = 10,
) -> Tuple[Optional[str], dict]:
    """Strip dim/price tokens from query and merge into filter dict.

    Returns (stripped_query_or_None, updated_filters_dict). Existing user params are
    never overwritten. Tolerance applies when a single value (not a range) is given.
    """
    if query is None:
        return None, existing
    s = str(query)
    if not s.strip():
        return None, existing

    consumed_spans: List[Tuple[int, int]] = []

    def _tol(n: float) -> Tuple[float, float]:
        return (max(0.0, n - tolerance_mm), n + tolerance_mm)

    # 1) L600 / W365 / H140 (+ optional range)
    for m in _AXIS_LETTER_RE.finditer(s):
        letter = m.group(1).lower()
        axis = {"l": "length", "w": "width", "h": "height"}[letter]
        lo = float(m.group(2))
        hi = float(m.group(3)) if m.group(3) else None
        if hi is None:
            lo_v, hi_v = _tol(lo)
        else:
            lo_v, hi_v = lo, hi
        _apply_axis_range(existing, axis, lo_v, hi_v)
        consumed_spans.append(m.span())

    # 2) "365 width" / "width 365" / "365mm long" etc.
    for m in _NUM_AXIS_RE.finditer(s):
        # group order: (n1, n2, axis_word_a) | (axis_word_b, n3, n4)
        if m.group(1):
            n1 = float(m.group(1))
            n2 = float(m.group(2)) if m.group(2) else None
            axis_word = m.group(3).lower()
        else:
            axis_word = m.group(4).lower()
            n1 = float(m.group(5))
            n2 = float(m.group(6)) if m.group(6) else None
        axis = _AXIS_WORD_TO_KEY.get(axis_word)
        if axis is None:
            continue
        if n2 is None:
            lo_v, hi_v = _tol(n1)
        else:
            lo_v, hi_v = n1, n2
        _apply_axis_range(existing, axis, lo_v, hi_v)
        consumed_spans.append(m.span())

    # 3) "365 mm" with no axis → any_dimension band
    for m in _NUM_MM_RE.finditer(s):
        # Skip if span already consumed by axis-aware match
        if any(s_start <= m.start() and m.end() <= s_end for s_start, s_end in consumed_spans):
            continue
        n1 = float(m.group(1))
        n2 = float(m.group(2)) if m.group(2) else None
        if n2 is None:
            lo_v, hi_v = _tol(n1)
        else:
            lo_v, hi_v = n1, n2
        if existing.get("any_dimension_min") is None:
            existing["any_dimension_min"] = lo_v
        if existing.get("any_dimension_max") is None:
            existing["any_dimension_max"] = hi_v
        consumed_spans.append(m.span())

    # 4) price tokens
    for m in _PRICE_RE.finditer(s):
        n1 = float(m.group(1))
        n2 = float(m.group(2)) if m.group(2) else None
        if n2 is None:
            lo_v, hi_v = n1, n1
        else:
            lo_v, hi_v = n1, n2
        if existing.get("price_min") is None:
            existing["price_min"] = lo_v
        if existing.get("price_max") is None:
            existing["price_max"] = hi_v
        consumed_spans.append(m.span())

    # Build stripped query: remove consumed spans, collapse whitespace.
    if not consumed_spans:
        return query, existing
    consumed_spans.sort()
    out_parts: List[str] = []
    cursor = 0
    for start, end in consumed_spans:
        if start < cursor:
            continue
        out_parts.append(s[cursor:start])
        cursor = end
    out_parts.append(s[cursor:])
    stripped = re.sub(r"\s+", " ", "".join(out_parts)).strip()
    return (stripped or None), existing


def _comma_separated_promotion_uuids(q: Optional[str]) -> Optional[List[str]]:
    """If *q* is 'uuid,uuid,...' (each segment a valid UUID), return normalized id strings; else None."""
    if q is None:
        return None
    s = str(q).strip()
    if not s or "," not in s:
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) < 2:
        return None
    out: List[str] = []
    for p in parts:
        try:
            out.append(str(uuid.UUID(p)))
        except ValueError:
            return None
    # stable dedupe
    return list(dict.fromkeys(out))


# Standalone endpoint for listing all promotion products
@router.get("/", response_model=PromotionServingListResponse[PromotionProductResponse])
async def list_all_promotion_products(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    sort: Optional[str] = Query("created_at"),
    dir: Optional[str] = Query("asc"),
    query: Optional[str] = Query(
        None,
        description="Tokenized text search (whitespace-split, AND across tokens) over Product.product_code, product_name, description, item_type, and Promotion.description.",
    ),
    entities: Optional[list[str]] = Query(
        None,
        description="DEPRECATED - free-text entity bag. Prefer `promotion_ids` / `product_ids`.",
    ),
    promotion_ids: Optional[list[str]] = Query(
        None,
        description="Canonical promotion UUIDs (csv/JSON/repeated).",
    ),
    product_ids: Optional[list[str]] = Query(
        None,
        description="Canonical product UUIDs (csv/JSON/repeated). Narrows to lines containing any of these products.",
    ),
    promotion_id: Optional[str] = Query(
        None,
        description="Legacy: single promotion UUID.",
    ),
    product_id: Optional[str] = Query(
        None,
        description="Legacy: filter promotion lines to a single product (UUID).",
    ),
    category_id: Optional[str] = Query(None, description="Product category UUID or category_code/name."),
    brand_id: Optional[str] = Query(None, description="Product brand UUID or brand_code/name."),
    item_type: Optional[str] = Query(None, description="Product item_type exact match."),
    status: Optional[str] = Query(None, description="Product status: active|inactive|all."),
    active: Optional[bool] = Query(
        None,
        description=(
            "Parent-promotion active filter (NOT product status - that's `status`). "
            "Omit: full catalog for interactive callers; API-key/MCP callers default "
            "to active-first. true: active-promotion lines (is_active and today within "
            "start/end), falling back to inactive-promotion lines when a narrowing "
            "filter yields zero matches. false: inactive-promotion lines only."
        ),
    ),
    price_min: Optional[float] = Query(None, description="Minimum Product.list_price (MYR)."),
    price_max: Optional[float] = Query(None, description="Maximum Product.list_price (MYR)."),
    length_min: Optional[float] = Query(None, description="Minimum dimensions_length in mm."),
    length_max: Optional[float] = Query(None, description="Maximum dimensions_length in mm."),
    width_min: Optional[float] = Query(None, description="Minimum dimensions_width in mm."),
    width_max: Optional[float] = Query(None, description="Maximum dimensions_width in mm."),
    height_min: Optional[float] = Query(None, description="Minimum dimensions_height in mm."),
    height_max: Optional[float] = Query(None, description="Maximum dimensions_height in mm."),
    any_dimension_min: Optional[float] = Query(
        None,
        description="Minimum value (mm) for ANY of length/width/height. Use for axis-agnostic queries like 'L600' / 'sides over 600mm'.",
    ),
    any_dimension_max: Optional[float] = Query(
        None,
        description="Maximum value (mm) for ANY of length/width/height.",
    ),
    access_levels: Optional[list[str]] = Query(
        None,
        description=(
            "Optional access-level NAMES filter (translated to codes via "
            "contact_access_types.name; intersection with promotion.access_levels)."
        ),
    ),
    serving_policy: bool = Query(
        False,
        description=(
            "Answer as the chatbot: gate lines on the per-type promotion policy "
            "of their PARENT promotion instead of the plain active gate. Lines "
            "whose parent has expired and whose type still honours it come back "
            "with `expired_but_usable: true`; lines under an expired special do "
            "not come back at all."
        ),
    ),
    current_user: dict = Depends(require_any_permission_with_api_key(PROMOTION_PRODUCT_READERS)),
    db: Session = Depends(get_db)
):
    """Get promotion products with pagination. Optional access_levels (names) intersection filter."""
    try:
        from app.services.contact_access_type_service import ContactAccessTypeService
        from app.services.entity_filter_helpers import normalize_list_query_param
        access_levels = normalize_list_query_param(access_levels)
        contact_codes = ContactAccessTypeService(db).translate_names_to_codes(access_levels)
        service = PromotionProductService(db)
        resolved_pid: Optional[str] = None
        resolved_pids: Optional[List[str]] = None
        text_query = query

        if promotion_id is not None and str(promotion_id).strip():
            resolved_pid = _resolve_promotion_id_for_filter(db, promotion_id)
            if resolved_pid is None:
                return {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
        else:
            multi = _comma_separated_promotion_uuids(query)
            if multi is not None:
                resolved_pids = []
                for uid in multi:
                    r = _resolve_promotion_id_for_filter(db, uid)
                    if r is None:
                        return {
                            "data": [],
                            "pagination": {"total": 0, "page": page, "limit": limit},
                            "empty": True,
                        }
                    resolved_pids.append(r)
                resolved_pids = list(dict.fromkeys(resolved_pids))
                text_query = None
            else:
                implicit_id, text_query = _implicit_promotion_and_text_query(query)
                if implicit_id:
                    resolved_pid = _resolve_promotion_id_for_filter(db, implicit_id)
                    if resolved_pid is None:
                        return {
                            "data": [],
                            "pagination": {"total": 0, "page": page, "limit": limit},
                            "empty": True,
                        }

        filter_state = {
            "length_min": length_min, "length_max": length_max,
            "width_min": width_min, "width_max": width_max,
            "height_min": height_min, "height_max": height_max,
            "any_dimension_min": any_dimension_min, "any_dimension_max": any_dimension_max,
            "price_min": price_min, "price_max": price_max,
        }
        text_query, filter_state = _parse_query_dimensions(text_query, filter_state)
        # Drop domain-noise tokens ("promotion", "promo", ...) from caller text.
        # Catalog in `services.query_normalizer.DOMAIN_STOPWORDS`.
        from app.services.query_normalizer import strip_domain_stopwords
        text_query = strip_domain_stopwords(text_query, "promotion")

        from app.services.entity_filter_helpers import normalize_entities_query_param
        # Merge resolved_pids (legacy) with new promotion_ids kwarg.
        new_promotion_ids = parse_uuid_list(promotion_ids, param_name="promotion_ids")
        new_product_ids = parse_uuid_list(product_ids, param_name="product_ids")
        if new_promotion_ids:
            merged_pids = list(resolved_pids or [])
            merged_pids.extend(new_promotion_ids)
            resolved_pids = list(dict.fromkeys(merged_pids))

        norm_entities = normalize_entities_query_param(entities)
        # Require at least one narrowing filter so callers cannot enumerate the
        # full catalog. `text_query` covers the free-text path (already stripped
        # of domain stopwords); `resolved_pid` / `resolved_pids` / explicit
        # `product_id` / `product_ids` cover canonical-UUID paths; `entities`
        # covers the deprecated free-text entity bag.
        has_narrowing_filter = bool(
            resolved_pid
            or resolved_pids
            or new_product_ids
            or (product_id and product_id.strip())
            or (text_query and text_query.strip())
            or norm_entities
        )
        # Gate "no filter = empty page" guard to API-key / MCP callers only.
        # FE DataGrid lands on this endpoint with no filters and expects the
        # full catalog; only chatbot/MCP needs whole-catalog enumeration guard.
        is_api_key_caller = (current_user or {}).get("auth_method") == "api_key"
        if is_api_key_caller and not has_narrowing_filter:
            return {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }

        # MCP/API-key callers default to active-first + inactive fallback (parent
        # promotion). Interactive (JWT) callers keep the full catalog unless they
        # pass `active` explicitly - preserves the FE DataGrid's "show all" listing.
        if active is None and is_api_key_caller:
            active = True

        result = service.list_promotion_products(
            promotion_id=resolved_pid,
            promotion_ids=resolved_pids,
            product_id=product_id,
            product_ids_filter=new_product_ids,
            page=page,
            limit=limit,
            sort_field=sort or "created_at",
            sort_dir=dir or "asc",
            query=text_query,
            entities=norm_entities,
            category_id=category_id,
            brand_id=brand_id,
            item_type=item_type,
            status=status,
            active=active,
            price_min=filter_state["price_min"],
            price_max=filter_state["price_max"],
            length_min=filter_state["length_min"],
            length_max=filter_state["length_max"],
            width_min=filter_state["width_min"],
            width_max=filter_state["width_max"],
            height_min=filter_state["height_min"],
            height_max=filter_state["height_max"],
            any_dimension_min=filter_state["any_dimension_min"],
            any_dimension_max=filter_state["any_dimension_max"],
            contact_access_codes=contact_codes,
            serving_policy=serving_policy,
        )
        # Map promo_selling_price to promotion_price for each product
        products = result.get("data", [])
        for product in products:
            if hasattr(product, 'promo_selling_price'):
                product.promotion_price = product.promo_selling_price
        result["data"] = products
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


# Note: POST, PUT, DELETE endpoints for promotion products are handled in the nested router
# under /promotions/{promotion_id}/products in promotions.py
