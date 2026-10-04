"""Per-resource `entities` plumbing.

Shared helper so each service that supports the `entities: list[str]` parameter
doesn't have to re-implement the same boilerplate (resolve, empty-guard, echo
payload). Each call site supplies:

    1. The free-text `entities` list.
    2. A `primary_bucket` callable that returns the bucket field whose presence
       gates whether the listing has anything to filter on (e.g. product_codes
       for stock balance / products list; shipment_numbers for incoming-stock
       shipments). If that bucket is empty after resolution, the helper builds
       an empty-list payload + echo so the caller can early-return.
    3. The allowed entity types for the resource.

Usage pattern:

    buckets = resolve_or_empty(db, entities, allowed_entity_types=(...))
    if buckets is not None and not buckets.product_codes:
        return empty_payload(buckets, page=page, limit=limit)
    # ... apply filter ...
    return attach_echo(payload, buckets)
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from app.services.entity_resolver import (
    EntityFilterBuckets,
    resolve_entities_to_filters,
)


# Every list tool that supports `entities` opts the resolver into the full type
# catalogue - RAG/substring scope is wider than what the tool can FILTER on,
# but echoing unfilterable types (e.g. matched a form but tool only filters by
# product) is useful context for the agent.
DEFAULT_ALLOWED_TYPES: tuple[str, ...] = (
    "product",
    "customer",
    "customer_order",
    "transporter",
    "inbound_shipment",
    "spo_allocation",
    "grn",
    "promotion",
    "attachment",
    "form",
    "supplier",
)


def resolve_or_empty(
    db: Session,
    entities: Optional[list[str]],
    *,
    allowed_entity_types: Iterable[str] = DEFAULT_ALLOWED_TYPES,
    regions=None,
) -> Optional[EntityFilterBuckets]:
    """Resolve `entities` to filter buckets, or return None when the caller passed nothing.

    Returns None when `entities` is falsy (nothing to do - caller proceeds with
    existing typed filters). Otherwise returns the populated `EntityFilterBuckets`
    so the caller can branch on which bucket fields the resource cares about.
    """
    if not entities:
        return None
    return resolve_entities_to_filters(
        db,
        entities,
        allowed_entity_types=tuple(allowed_entity_types),
        regions=regions,
    )


def empty_payload(
    buckets: EntityFilterBuckets,
    *,
    page: int,
    limit: int,
) -> dict[str, Any]:
    """Empty list response + echo for the case where `entities` resolved nothing
    that the resource can filter by (e.g. user passed a customer name to a
    product-only listing). The caller returns this directly to short-circuit."""
    return {
        "data": [],
        "pagination": {"total": 0, "page": page, "limit": limit},
        "empty": True,
        "resolved_entities": buckets.as_echo(),
    }


def attach_echo(payload: dict[str, Any], buckets: Optional[EntityFilterBuckets]) -> dict[str, Any]:
    """Mutate-and-return `payload` with `resolved_entities` echo when buckets is non-None."""
    if buckets is not None:
        payload["resolved_entities"] = buckets.as_echo()
    return payload


def normalize_list_query_param(raw: Optional[list[str]]) -> Optional[list[str]]:
    """Flatten any list-shaped FastAPI Query param.

    Accepts None / repeated strings / JSON-array string / comma-separated string.
    Use for any `Optional[list[str]] = Query(None)` param that LLM callers may
    serialize as a single comma-separated string (e.g. "A,B,C") or a JSON literal
    (e.g. '["A","B"]') instead of repeated `?k=A&k=B` query params.
    """
    if raw is None:
        return None
    import json as _json

    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item is None:
            continue
        s = str(item).strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = _json.loads(s)
                if isinstance(parsed, list):
                    for p in parsed:
                        ps = str(p).strip()
                        key = ps.lower()
                        if ps and key not in seen:
                            seen.add(key)
                            out.append(ps)
                    continue
            except Exception:
                pass
        for piece in s.split(","):
            piece = piece.strip()
            if not piece:
                continue
            key = piece.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(piece)
    return out or None


# Backwards-compatible alias for callers that imported the entities-specific name.
normalize_entities_query_param = normalize_list_query_param
