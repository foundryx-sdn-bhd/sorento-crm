"""The `spec_constraints` WHERE for the product list and stock balance routes.

SPEC-PROMO-PARAMS (`documentation/plans/chatbot/CARD-spec-promo-params-12oct.md` section 2).
Unlike the ranker in `product_spec_search` (every spec a boost, never a WHERE), this is a
hard filter: constraints AND together, and a product with no recorded value for a key (or no
`product_specifications` row at all) is not a match.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Float, and_, case, cast, func, or_, select, true
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.product_spec import ProductSpecifications
from app.services.error_handler import AppException
from app.services.product_predicate_service import active_promotion_clause
from app.services.product_spec_registry import active_registry
from app.services.product_spec_search import BRAND_KEY, membership_clause

OPS = ("eq", "between", "gte", "lte")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _bad(message: str) -> AppException:
    return AppException(422, message, code="INVALID_SPEC_CONSTRAINTS")


def parse_spec_constraints(db: Session, raw: str | None) -> list[dict] | None:
    """`[{key, op, value}]` from the query string; None when blank. 422 on anything malformed,
    an op outside eq|between|gte|lte, or a key that is not an active registry key."""
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise _bad("`spec_constraints` must be a JSON list of {key, op, value}.")
    if not isinstance(parsed, list) or not parsed:
        raise _bad("`spec_constraints` must be a non-empty JSON list of {key, op, value}.")
    types = {row.spec_key: row.data_type for row in active_registry(db)}
    out: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or "value" not in item:
            raise _bad("Each spec constraint needs a `key` and a `value`.")
        key, op, value = item["key"], item.get("op") or "eq", item["value"]
        if key not in types:
            raise _bad(f"Unknown spec key '{key}'.")
        if op not in OPS:
            raise _bad(f"Unknown op '{op}' for spec key '{key}'; use eq, between, gte or lte.")
        if op == "eq":
            scalars = value if isinstance(value, list) else [value]
            if not scalars or not all(isinstance(v, (str, int, float, bool)) for v in scalars):
                raise _bad(f"Spec key '{key}': `value` must be a string, number, boolean or a list of them.")
        elif types[key] != "numeric":
            raise _bad(f"Spec key '{key}' is not numeric; op '{op}' does not apply.")
        elif op == "between":
            if not (isinstance(value, list) and len(value) == 2 and all(_is_number(v) for v in value)):
                raise _bad(f"Spec key '{key}': `between` takes [lo, hi].")
        elif not _is_number(value):
            raise _bad(f"Spec key '{key}': `{op}` takes a number.")
        out.append({"key": key, "op": op, "value": value})
    return out


def _stored(key: str):
    return ProductSpecifications.values[key]["value"]


def _one(c: dict):
    """One constraint as a predicate over `ProductSpecifications`."""
    key, op, value = c["key"], c["op"], c["value"]
    if op == "eq":
        scalars = value if isinstance(value, list) else [value]
        if all(isinstance(v, bool) for v in scalars):
            stored = _stored(key)
            return and_(
                func.jsonb_typeof(stored) == "boolean",
                stored.astext.in_(["true" if v else "false" for v in scalars]),
            )
        return membership_clause({key: [v for v in scalars if not isinstance(v, bool)]})
    stored = _stored(key)
    number = case((func.jsonb_typeof(stored) == "number", cast(stored.astext, Float)))
    if op == "between":
        return number.between(min(value), max(value))
    return number >= value if op == "gte" else number <= value


def spec_constraint_clause(db: Session, constraints: list[dict]):
    """Predicate on `Product.id`: a spec row exists for the product and satisfies every constraint."""
    return Product.id.in_(
        select(ProductSpecifications.product_id).where(and_(*[_one(c) for c in constraints]))
    )


def product_filter_clause(
    db: Session,
    constraints: list[dict] | None,
    has_active_promotion: bool = False,
    access_levels: list[str] | None = None,
):
    """The spec and promotion filters as one predicate over Product, or None when neither is set.
    `access_levels` (the asking contact's tier, filled by the caller) only narrows the promotion."""
    parts = []
    if constraints:
        parts.append(spec_constraint_clause(db, constraints))
    if has_active_promotion:
        parts.append(active_promotion_clause(db, access_levels))
    return and_(*parts) if parts else None


def _sort_key(v: Any):
    return (0, v, "") if _is_number(v) else (1, 0, str(v).lower())


def spec_miss_detail(db: Session, constraints: list[dict]) -> dict:
    """For a filtered zero: per constrained key, the values that exist among products matching
    the OTHER constraints (`spec_alternatives`) and how many of those have no value recorded
    for it (`spec_unrecorded`, zero counts left out). The reply must never say "none exists"
    while products with nothing recorded remain."""
    alternatives: list[dict] = []
    unrecorded: dict[str, int] = {}
    for key in dict.fromkeys(c["key"] for c in constraints):
        if key == BRAND_KEY:
            continue  # the brand lives on the product, not in the stored values
        others = [_one(c) for c in constraints if c["key"] != key]
        where = and_(*others) if others else true()
        stored = _stored(key)
        present = and_(stored.isnot(None), func.jsonb_typeof(stored) != "null")
        seen: dict[str, Any] = {}
        for (value,) in db.execute(select(stored).where(where, present).distinct()).all():
            for v in value if isinstance(value, list) else [value]:
                seen.setdefault(json.dumps(v, sort_keys=True), v)
        if seen:
            alternatives.append({"key": key, "values": sorted(seen.values(), key=_sort_key)})
        missing = db.execute(
            select(func.count(func.distinct(ProductSpecifications.product_id))).where(
                where, or_(stored.is_(None), func.jsonb_typeof(stored) == "null")
            )
        ).scalar()
        if missing:
            unrecorded[key] = int(missing)
    return {"spec_alternatives": alternatives, "spec_unrecorded": unrecorded}
