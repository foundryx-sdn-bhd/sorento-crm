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
# The virtual key for a size said with no axis word ("250mm"). Not a registry key: the route
# resolves it to the one active numeric mm key whose stored value holds the number among the
# products matching the other constraints (card section 9, slice 1).
SIZE_KEY = "size"
CLASS_KEY = "class"


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _bad(message: str) -> AppException:
    return AppException(422, message, code="INVALID_SPEC_CONSTRAINTS")


def parse_spec_constraints(
    db: Session, raw: str | None, hidden: frozenset[str] = frozenset()
) -> list[dict] | None:
    """`[{key, op, value}]` from the query string; None when blank. 422 on anything malformed,
    an op outside eq|between|gte|lte, or a key that is not an active registry key.

    `hidden` (the asking contact's hidden keys, `request_hidden_keys`): a constraint on one
    is dropped after validation, neither applied nor echoed, the way the references
    resolver drops a hidden key before it ranks (AC-18). None when nothing is left."""
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise _bad("`spec_constraints` must be a JSON list of {key, op, value}.")
    if not isinstance(parsed, list) or not parsed:
        raise _bad("`spec_constraints` must be a non-empty JSON list of {key, op, value}.")
    types: dict[str, str] = {str(row.spec_key): str(row.data_type or "") for row in active_registry(db)}
    types.setdefault(SIZE_KEY, "numeric")
    # `class` is what the product IS, its vocabulary the categories' `class_label` (the
    # registry row carries no choices of its own), so it is known even on an install whose
    # registry has no row for it.
    types.setdefault(CLASS_KEY, "enum")
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
            if key == SIZE_KEY and not all(_is_number(v) for v in scalars):
                raise _bad(f"Spec key '{SIZE_KEY}' takes a number in mm.")
        elif types[key] != "numeric":
            raise _bad(f"Spec key '{key}' is not numeric; op '{op}' does not apply.")
        elif op == "between":
            if not (isinstance(value, list) and len(value) == 2 and all(_is_number(v) for v in value)):
                raise _bad(f"Spec key '{key}': `between` takes [lo, hi].")
        elif not _is_number(value):
            raise _bad(f"Spec key '{key}': `{op}` takes a number.")
        if key not in hidden:
            out.append({"key": key, "op": op, "value": value})
    return out or None


def request_hidden_keys(db: Session, contact_id: str | None, space_id: str | None) -> frozenset[str]:
    """The registry keys hidden from the contact a chatbot call is asked for; none on a
    staff call (no `contact_id`)."""
    if not contact_id:
        return frozenset()
    from app.services.spec_visibility import contact_hidden_keys

    return contact_hidden_keys(db, contact_id, space_id)


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


def _scoped(*columns):
    """A read of `product_specifications` joined to its product, so the company scope the
    session carries (`products` is company scoped, `do_orm_execute`) holds here as it does
    on the outer product query: a foreign company's product never contributes a value, a
    size candidate or an unrecorded count."""
    return select(*columns).join(Product, Product.id == ProductSpecifications.product_id)


def _where(constraints: list[dict]):
    return and_(*[_one(c) for c in constraints]) if constraints else true()


def _present(key: str):
    stored = _stored(key)
    return and_(stored.isnot(None), func.jsonb_typeof(stored) != "null")


def _values_of(db: Session, key: str, where) -> list:
    """The distinct stored values of `key` among the spec rows matching `where`, sorted."""
    stored = _stored(key)
    seen: dict[str, Any] = {}
    for (value,) in db.execute(_scoped(stored).where(where, _present(key)).distinct()).all():
        for v in value if isinstance(value, list) else [value]:
            seen.setdefault(json.dumps(v, sort_keys=True), v)
    return sorted(seen.values(), key=_sort_key)


def _alternative(row, values: list) -> dict:
    return {"key": str(row.spec_key), "label": row.label, "unit": row.unit, "values": values}


def spec_miss_detail(db: Session, constraints: list[dict]) -> dict:
    """For a filtered zero: per constrained key, the values that exist among products matching
    the OTHER constraints (`spec_alternatives`, each item with the key's label and unit so the
    reply can name them) and how many of those have no value recorded for it
    (`spec_unrecorded`, zero counts left out), plus `spec_labels` for every key named. The
    reply must never say "none exists" while products with nothing recorded remain."""
    registry = {str(row.spec_key): row for row in active_registry(db)}
    alternatives: list[dict] = []
    unrecorded: dict[str, int] = {}
    for key in dict.fromkeys(c["key"] for c in constraints):
        if key == BRAND_KEY or key not in registry:
            continue  # the brand lives on the product, not in the stored values
        where = _where([c for c in constraints if c["key"] != key])
        values = _values_of(db, key, where)
        if values:
            alternatives.append(_alternative(registry[key], values))
        stored = _stored(key)
        missing = db.execute(
            _scoped(func.count(func.distinct(ProductSpecifications.product_id))).where(
                where, or_(stored.is_(None), func.jsonb_typeof(stored) == "null")
            )
        ).scalar()
        if missing:
            unrecorded[key] = int(missing)
    named = {a["key"] for a in alternatives} | set(unrecorded)
    return {
        "spec_alternatives": alternatives,
        "spec_unrecorded": unrecorded,
        # Each named key's label, so the reply can say "Length" for a key with nothing recorded.
        "spec_labels": {key: str(registry[key].label) for key in sorted(named)},
    }


def resolve_size(
    db: Session, constraints: list[dict] | None, hidden: frozenset[str] = frozenset()
) -> tuple[list[dict] | None, dict]:
    """Resolve the virtual `size` key against the data, before the route filters.

    Candidates are the active numeric registry keys in mm. Among products matching the other
    constraints, the candidates holding the number decide:
      * one key: the constraint is rewritten onto it; extra = `{"spec_resolved": {"size": key}}`.
      * several: the route returns no rows; extra = `spec_ambiguous` (candidates sorted) plus
        `spec_labels` (each candidate's label, so the reply can ask "Length or Width?").
      * none: the route returns no rows; extra = `spec_alternatives` per mm key present and
        `spec_unrecorded: {"size": n}` (products with no mm value at all, left out when 0).
    Returns `(constraints, extra)`; constraints is None when the route must return no rows.
    No `size` constraint: `(constraints, {})` unchanged. A key in `hidden` (the asking
    contact's) is never a candidate and never listed; a product holding only hidden mm
    values counts as having none recorded."""
    if not constraints or not any(c["key"] == SIZE_KEY for c in constraints):
        return constraints, {}
    mm_keys = [
        row for row in active_registry(db)
        if str(row.data_type) == "numeric" and str(row.unit or "").lower() == "mm"
        and str(row.spec_key) not in hidden
    ]
    out = [c for c in constraints if c["key"] != SIZE_KEY]
    resolved: dict[str, str] = {}
    for size in [c for c in constraints if c["key"] == SIZE_KEY]:
        where = _where(out)
        holders = sorted(
            str(row.spec_key) for row in mm_keys
            if db.execute(
                _scoped(ProductSpecifications.product_id)
                .where(where, _one({**size, "key": str(row.spec_key)}))
                .limit(1)
            ).first()
        )
        if len(holders) == 1:
            out.append({**size, "key": holders[0]})
            resolved[SIZE_KEY] = holders[0]
            continue
        if holders:
            labels = {str(row.spec_key): str(row.label) for row in mm_keys if str(row.spec_key) in holders}
            return None, {
                "spec_ambiguous": [
                    {"key": SIZE_KEY, "value": size["value"], "candidates": holders}
                ],
                "spec_labels": labels,
            }
        alternatives = [
            _alternative(row, values)
            for row in mm_keys
            if (values := _values_of(db, str(row.spec_key), where))
        ]
        missing = db.execute(
            _scoped(func.count(func.distinct(ProductSpecifications.product_id))).where(
                where, *[~_present(str(row.spec_key)) for row in mm_keys]
            )
        ).scalar()
        extra: dict = {"spec_alternatives": alternatives, "spec_unrecorded": {}}
        if missing:
            extra["spec_unrecorded"] = {SIZE_KEY: int(missing)}
        return None, extra
    return out, {"spec_resolved": resolved}
