"""The `spec_constraints` WHERE for the product list and stock balance routes.

SPEC-PROMO-PARAMS (`documentation/plans/chatbot/CARD-spec-promo-params-12oct.md` section 2).
Unlike the ranker in `product_spec_search` (every spec a boost, never a WHERE), this is a
hard filter: constraints AND together, and a product with no recorded value for a key (or no
`product_specifications` row at all) is not a match.
"""
from __future__ import annotations

import json
import math
from typing import Any

from sqlalchemy import Float, and_, case, cast, false, func, or_, select, true
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
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
# Bounds on what one request may ask (security review S1): the raw text, the number of
# constraints, and the values one `eq` may list.
MAX_RAW_CHARS = 4096
MAX_CONSTRAINTS = 16
MAX_EQ_VALUES = 50


def _is_number(v: Any) -> bool:
    """A finite JSON number (never a bool, never NaN or Infinity, which Python's json reads)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


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
    if len(raw) > MAX_RAW_CHARS:
        raise _bad(f"`spec_constraints` is longer than {MAX_RAW_CHARS} characters.")
    try:
        parsed = json.loads(raw)
    except (ValueError, RecursionError):  # JSONDecodeError is a ValueError; deep nesting recurses
        raise _bad("`spec_constraints` must be a JSON list of {key, op, value}.")
    if not isinstance(parsed, list) or not parsed:
        raise _bad("`spec_constraints` must be a non-empty JSON list of {key, op, value}.")
    if len(parsed) > MAX_CONSTRAINTS:
        raise _bad(f"`spec_constraints` takes at most {MAX_CONSTRAINTS} constraints.")
    types: dict[str, str] = {str(row.spec_key): str(row.data_type or "") for row in active_registry(db)}
    types.setdefault(SIZE_KEY, "numeric")
    # `class` is what the product IS, its vocabulary the categories' `class_label` (the
    # registry row carries no choices of its own), so it is known even on an install whose
    # registry has no row for it (legacy / category-sourced). A class row that exists and
    # is switched off is honoured: the key is then unknown (422) like any inactive key.
    if CLASS_KEY not in types and not db.query(
        db.query(ProductSpecRegistry).filter(ProductSpecRegistry.spec_key == CLASS_KEY).exists()
    ).scalar():
        types[CLASS_KEY] = "enum"
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
            if not scalars or len(scalars) > MAX_EQ_VALUES:
                raise _bad(f"Spec key '{key}': `value` takes 1 to {MAX_EQ_VALUES} values.")
            # The value's type is the key's type (security review N3): a number on an enum key
            # or text on a numeric one would only ever match nothing, silently.
            kind = types[key]
            if kind == "numeric":
                ok, want = all(_is_number(v) for v in scalars), "a number"
            elif kind == "boolean":
                ok, want = all(isinstance(v, bool) for v in scalars), "true or false"
            else:
                ok, want = all(isinstance(v, str) for v in scalars), "text"
            if not ok:
                raise _bad(f"Spec key '{key}': `value` must be {want}.")
        elif types[key] != "numeric":
            raise _bad(f"Spec key '{key}' is not numeric; op '{op}' does not apply.")
        elif op == "between":
            if not (isinstance(value, list) and len(value) == 2 and all(_is_number(v) for v in value)):
                raise _bad(f"Spec key '{key}': `between` takes [lo, hi].")
        elif not _is_number(value):
            raise _bad(f"Spec key '{key}': `{op}` takes a number.")
        constraint = {"key": key, "op": op, "value": value}
        if key not in hidden and constraint not in out:  # an identical repeat is said once
            out.append(constraint)
    return out or None


def request_tier(
    db: Session, contact_id: str | None, space_id: str | None, access_levels: list[str] | None
) -> list[str] | None:
    """The tier `has_active_promotion` is read under (security review S2). An explicit
    `access_levels` wins; a staff call (no `contact_id`) has none (None, no restriction);
    otherwise the asking contact's OWN access codes, resolved the way the spec visibility
    is (`resolve_contact_with_null_workspace_fallback`). A contact that does not resolve or
    holds no code gets `[]`: fail closed, no promotion counts (`product_filter_clause`)."""
    if access_levels or not contact_id:
        return access_levels
    from app.services.contact_access_type_service import ContactAccessTypeService
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    resolved = resolve_contact_with_null_workspace_fallback(db, contact_id=contact_id, space_id=space_id)
    return ContactAccessTypeService(db).get_contact_access_codes(str(resolved)) if resolved else []


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
    """One constraint as a predicate over `ProductSpecifications`. A resolved `size` held on
    several axes carries `any_of` (one constraint per axis) and matches any of them."""
    if c.get("any_of"):
        return or_(*[_one(x) for x in c["any_of"]])
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
        parts.append(promoted_clause(db, access_levels))
    return and_(*parts) if parts else None


def promoted_clause(db: Session, access_levels: list[str] | None):
    """Product is in a promotion live today, visible to `access_levels` (names or codes) and
    of its own company (`active_promotion_clause`). `[]` is a tier that resolved to nothing
    (`request_tier`): no promotion counts. Also the base the size resolution and the miss
    detail read under when the answer is about promoted products only."""
    return false() if access_levels == [] else active_promotion_clause(db, access_levels)


def _sort_key(v: Any):
    return (0, v, "") if _is_number(v) else (1, 0, str(v).lower())


def _scoped(*columns):
    """A read of `product_specifications` joined to its product, so the company scope the
    session carries (`products` is company scoped, `do_orm_execute`) holds here as it does
    on the outer product query: a foreign company's product never contributes a value, a
    size candidate or an unrecorded count."""
    return select(*columns).join(Product, Product.id == ProductSpecifications.product_id)


def _where(constraints: list[dict], base=None):
    """The constraints ANDed, plus `base` (a predicate over Product, e.g. "in a promotion
    live today"), so the set read is the set the route answers about."""
    parts = [_one(c) for c in constraints] + ([base] if base is not None else [])
    return and_(*parts) if parts else true()


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


def spec_miss_detail(db: Session, constraints: list[dict], base=None) -> dict:
    """For a filtered zero: per constrained key, the values that exist among products matching
    the OTHER constraints (`spec_alternatives`, each item with the key's label and unit so the
    reply can name them) and how many of those have no value recorded for it
    (`spec_unrecorded`, zero counts left out), plus `spec_labels` for every key named. The
    reply must never say "none exists" while products with nothing recorded remain.

    Counts cover products that HAVE a `product_specifications` row; a product with no row at
    all is in neither number. `base` (a Product predicate) narrows the set read to the one
    the route answers about (the promoted products, on the promotion paths)."""
    registry = {str(row.spec_key): row for row in active_registry(db)}
    alternatives: list[dict] = []
    unrecorded: dict[str, int] = {}
    for key in dict.fromkeys(c["key"] for c in constraints):
        if key == BRAND_KEY or key not in registry:
            continue  # the brand lives on the product, not in the stored values
        where = _where([c for c in constraints if c["key"] != key], base)
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


def stamp_spec_page(payload: dict, constraints: list[dict] | None) -> None:
    """`spec_page` on a spec-filtered answer: the page it returned and how many matched, so the
    chatbot can show a few matches first and offer the rest (owner ruling, 12 Oct 2026). Only
    when `spec_constraints` were sent; every other caller's body is unchanged."""
    if not constraints:
        return
    page = payload.get("pagination")
    page = page.model_dump() if hasattr(page, "model_dump") else dict(page or {})
    payload["spec_page"] = {k: int(page.get(k) or 0) for k in ("page", "limit", "total")}


def resolve_size(
    db: Session, constraints: list[dict] | None, hidden: frozenset[str] = frozenset(), base=None
) -> tuple[list[dict] | None, dict]:
    """Resolve the virtual `size` key against the data, before the route filters.

    Candidates are the active numeric registry keys in mm. Among products matching the other
    constraints, the candidates holding the number decide:
      * one key: the constraint is rewritten onto it; extra = `{"spec_resolved": {"size": key}}`.
      * several: a product holding the number on ANY of them matches (owner ruling, 12 Oct
        2026, after hand test step 1: no "Length or Width?" question); the constraint becomes
        `{"key": "size", ..., "any_of": [one per axis]}`; extra = `{"spec_resolved": {"size":
        [keys, sorted]}}`.
      * none: the route returns no rows; extra = `spec_alternatives` per mm key present and
        `spec_unrecorded: {"size": n}` (products with no mm value at all, left out when 0).
    Returns `(constraints, extra)`; constraints is None when the route must return no rows.
    No `size` constraint: `(constraints, {})` unchanged. A key in `hidden` (the asking
    contact's) is never a candidate and never listed; a product holding only hidden mm
    values counts as having none recorded. `base` (a Product predicate) narrows the set read,
    as in `spec_miss_detail`; the unrecorded count, like there, covers products that have a
    `product_specifications` row."""
    if not constraints or not any(c["key"] == SIZE_KEY for c in constraints):
        return constraints, {}
    mm_keys = [
        row for row in active_registry(db)
        if str(row.data_type) == "numeric" and str(row.unit or "").lower() == "mm"
        and str(row.spec_key) not in hidden
    ]
    out = [c for c in constraints if c["key"] != SIZE_KEY]
    resolved: dict[str, str | list[str]] = {}
    for size in [c for c in constraints if c["key"] == SIZE_KEY]:
        where = _where(out, base)
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
            out.append({**size, "any_of": [{**size, "key": key} for key in holders]})
            resolved[SIZE_KEY] = holders
            continue
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
        # Owner ruling 13 Oct 2026 (size-miss wording, option b): "No 250 mm. Sizes on file: ...";
        # the reply echoes the size asked, so the route says which.
        # One size asked only: a range or a bound has no single size to echo (review S1).
        if (size.get("op") or "eq") == "eq" and isinstance(size.get("value"), (int, float)):
            extra["spec_size_asked"] = {"value": size["value"], "unit": "mm"}
        return None, extra
    return out, {"spec_resolved": resolved}
