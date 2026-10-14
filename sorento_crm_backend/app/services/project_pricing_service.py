"""Price floors and series membership: the two guardrails on a quotation line.

Both are deterministic lookups over configuration, kept out of the quotation service
so they can be specified as a golden set (``tests/test_project_price_floor.py``) and
reused by anything else that needs to ask "is this price allowed" -- a PO check in S4
will want the same answer.

The floor resolves by SPECIFICITY: product > its category > that category's ancestors,
nearest first > system. The level is implied by which key the rule carries, never
stored, so there is one source of truth for it.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.product import Product, ProductCategory
from app.models.product_set import ProductSet
from app.models.projects import (
    FLOOR_MODE_ABSOLUTE,
    FLOOR_MODE_PERCENT,
    FLOOR_MODES,
    PriceFloorRule,
    ProjectSeries,
    ProjectSeriesCategory,
    ProjectSeriesProduct,
    ProjectSeriesSet,
)
from app.services.error_handler import AppException
# The repo's ONE product-code normaliser (lowercased, dash/whitespace stripped), already
# symmetric with the SQL form used by the resolver and the variant graph. The client's
# sheet carries `CWB 242`, `SRTPW0035 ` and ` TPE 9203`; a tenth private spelling of this
# rule is how two surfaces end up disagreeing about whether a code matched.
from app.services.variant_link_service import normalize_code

#: A floor that came from the SERIES rather than from `price_floor_rules`. Carried on the
#: resolved floor so a breach can say WHICH policy bound the line - a refusal nobody can
#: trace is one nobody can act on (AC-C5).
LEVEL_SERIES = "series"
LEVEL_PRODUCT = "product"
LEVEL_CATEGORY = "category"
LEVEL_CATEGORY_ANCESTOR = "category_ancestor"
LEVEL_SYSTEM = "system"

_CENTS = Decimal("0.01")
# Depth guard for the category walk. A cycle in parent_category_id would otherwise spin
# forever; the real hierarchy is three deep.
_MAX_CATEGORY_DEPTH = 20


@dataclass(frozen=True)
class FloorSource:
    """The RULE that governs a target, and where it sits relative to that target.

    Distinct from ``ResolvedFloor``, which carries a resolved ringgit amount. A category
    has no list price, so a percentage rule on it can never become money -- there is a
    rule in force and no amount to print, and the two facts have to travel separately or
    the UI ends up inventing a number.
    """

    rule: PriceFloorRule
    level: str
    category_id: Optional[str] = None


@dataclass(frozen=True)
class ResolvedFloor:
    """The floor in force for one line, and where it came from.

    ``level`` and ``category_id`` are carried so the UI can explain a breach ("below
    the Basins floor of RM 850") instead of stating a bare number the salesperson
    cannot argue with or learn from.
    """

    value: Decimal
    mode: str
    level: str
    rule_id: str
    category_id: Optional[str] = None


@dataclass(frozen=True)
class SeriesMembership:
    """A series expanded once: its covered categories AND its named products (S18).

    Carried as one value rather than two loose sets so a caller cannot expand half of it
    and silently answer the wrong question - which is what a bare ``covered`` set of
    categories would now do, since it would say "not in the series" for every product
    nominated by name.
    """

    categories: Set[str]
    products: Set[str]


# --------------------------------------------------------------- category walk


def category_ancestry(db: Session, category_id: Optional[str]) -> List[str]:
    """``[self, parent, grandparent, ...]``, nearest first.

    Nearest-first is the whole point: the order of this list IS the precedence order
    for the ancestor levels, so the resolver never has to compute depth.
    """
    if not category_id:
        return []

    chain: List[str] = []
    seen: Set[str] = set()
    current: Optional[str] = category_id
    while current and current not in seen and len(chain) < _MAX_CATEGORY_DEPTH:
        chain.append(current)
        seen.add(current)
        row = (
            db.query(ProductCategory.parent_category_id)
            .filter(ProductCategory.id == current)
            .first()
        )
        current = row[0] if row else None
    return chain


def category_with_descendants(db: Session, category_ids: Sequence[str]) -> Set[str]:
    """Expand nominated categories DOWNWARD (AC-E5).

    Nominating "Sanitary Ware" into a series covers every basin and pan under it, which
    is what makes a series a short list of groups rather than a list of every SKU that
    goes stale the week after it is written.
    """
    frontier = {cid for cid in category_ids if cid}
    if not frontier:
        return set()

    covered: Set[str] = set(frontier)
    depth = 0
    while frontier and depth < _MAX_CATEGORY_DEPTH:
        rows = (
            db.query(ProductCategory.id)
            .filter(ProductCategory.parent_category_id.in_(frontier))
            .all()
        )
        children = {row[0] for row in rows} - covered
        covered |= children
        frontier = children
        depth += 1
    return covered


# ------------------------------------------------------------ floor resolution


def _floor_from_rule(rule: PriceFloorRule, list_price: Optional[Decimal]) -> Optional[Decimal]:
    if rule.mode == FLOOR_MODE_ABSOLUTE:
        return Decimal(rule.value).quantize(_CENTS)
    # Percent needs a base. A missing or zero list price yields NO floor rather than
    # zero: zero would mark the line compliant, which reads as "checked and fine".
    if not list_price or Decimal(list_price) <= 0:
        return None
    return (Decimal(list_price) * Decimal(rule.value) / Decimal(100)).quantize(_CENTS)


def resolve_floor(
    db: Session,
    *,
    company_id: str,
    product: Optional[Product],
    list_price: Optional[Decimal] = None,
    series_id: Optional[str] = None,
    series_pricing: Optional[Mapping[str, tuple]] = None,
) -> Optional[ResolvedFloor]:
    """The floor for one line, or None when no policy reaches it.

    None is a real answer and not an error: most installs start with no rules at all,
    and inventing a floor of zero would flag nothing while looking like it had checked.

    An off-catalog line (``product is None``) has no list price and no category, so
    there is nothing to resolve against. It is caught by the non-standard SKU alert
    instead (AC-E5).
    """
    if product is None:
        return None

    # The SERIES wins where it has an opinion (AC-C3). A scope quoted from a series is a
    # commercial deal already struck: the price was agreed and the margin with it, so a
    # generic `price_floor_rules` entry must not quietly override the number in the
    # contract. Where the series is silent - product not named, or missing either half of
    # the answer - it falls through and the rules apply exactly as they always did (AC-C4).
    if series_id is not None:
        pricing_for = (
            series_pricing
            if series_pricing is not None
            else series_pricing_map(db, series_id)
        )
        stated = pricing_for.get(product.id)
        if stated is not None:
            floor = series_floor(stated[0], stated[1])
            if floor is not None:
                return ResolvedFloor(
                    value=floor,
                    mode=FLOOR_MODE_ABSOLUTE,
                    level=LEVEL_SERIES,
                    # The series IS the policy here, so it is what a breach cites.
                    rule_id=str(series_id),
                )

    effective_list = list_price if list_price is not None else product.list_price
    rules = _active_rules(db, company_id)
    if not rules:
        return None

    by_product = {r.product_id: r for r in rules if r.product_id}
    by_category = {r.category_id: r for r in rules if r.category_id and not r.product_id}
    system = next((r for r in rules if not r.product_id and not r.category_id), None)

    # Specificity order, resolved in one pass. The first candidate that yields a usable
    # value wins; a percent rule with no list price to apply to falls through to the
    # next level rather than blocking the line.
    ancestry = category_ancestry(db, product.category_id)
    candidates: List[tuple[PriceFloorRule, str, Optional[str]]] = []
    if product.id in by_product:
        candidates.append((by_product[product.id], LEVEL_PRODUCT, None))
    for index, category_id in enumerate(ancestry):
        rule = by_category.get(category_id)
        if rule:
            level = LEVEL_CATEGORY if index == 0 else LEVEL_CATEGORY_ANCESTOR
            candidates.append((rule, level, category_id))
    if system:
        candidates.append((system, LEVEL_SYSTEM, None))

    for rule, level, category_id in candidates:
        value = _floor_from_rule(rule, effective_list)
        if value is None:
            continue
        return ResolvedFloor(
            value=value,
            mode=rule.mode,
            level=level,
            rule_id=rule.id,
            category_id=category_id,
        )
    return None


def resolve_category_floor_source(
    db: Session, *, company_id: str, category_id: Optional[str]
) -> Optional[FloorSource]:
    """The rule in force for a CATEGORY: itself, then its ancestors nearest first, then
    the company default.

    Same precedence as ``resolve_floor``, one level shorter because a category is not a
    product and there is nothing more specific than itself. There is no percent
    fall-through here either: that rule exists because a percentage of a missing list
    price is not a floor, and a category has no list price to miss.
    """
    if not category_id:
        return None

    rules = _active_rules(db, company_id)
    if not rules:
        return None

    by_category = {r.category_id: r for r in rules if r.category_id and not r.product_id}
    for index, ancestor_id in enumerate(category_ancestry(db, category_id)):
        rule = by_category.get(ancestor_id)
        if rule:
            level = LEVEL_CATEGORY if index == 0 else LEVEL_CATEGORY_ANCESTOR
            return FloorSource(rule=rule, level=level, category_id=ancestor_id)

    system = next((r for r in rules if not r.product_id and not r.category_id), None)
    return FloorSource(rule=system, level=LEVEL_SYSTEM) if system else None


def own_floor_rule(
    db: Session,
    *,
    company_id: str,
    product_id: Optional[str] = None,
    category_id: Optional[str] = None,
) -> Optional[PriceFloorRule]:
    """The rule set ON this exact target, inherited rules excluded.

    Whether the target owns one decides whether "clear this floor" is even offered, so it
    is a different question from what governs it, and the answer must not be inferred
    from the resolved level (a product-level resolution says the product owns a rule; a
    category-level one does not say whether THAT category is the one carrying it).

    Inactive rules count as owned: an inactive rule is skipped when resolving but still
    occupies the target's slot, and hiding it would offer "set a floor" for a target that
    already has one.
    """
    if not product_id and not category_id:
        return None
    return (
        db.query(PriceFloorRule)
        .filter(
            PriceFloorRule.company_id == company_id,
            PriceFloorRule.product_id == product_id
            if product_id
            else PriceFloorRule.product_id.is_(None),
            PriceFloorRule.category_id == category_id
            if category_id
            else PriceFloorRule.category_id.is_(None),
        )
        .first()
    )


SYSTEM_SOURCE_LABEL = "Company default"


def serialize_floor_rules(db: Session, rules: Sequence[PriceFloorRule]) -> List[dict]:
    """Rules with their targets NAMED. One place, so the pricing screen and the
    master-data editors cannot disagree about what a rule is called."""
    product_ids = {r.product_id for r in rules if r.product_id}
    category_ids = {r.category_id for r in rules if r.category_id}
    products = (
        {
            row.id: row.product_code
            for row in db.query(Product).filter(Product.id.in_(product_ids)).all()
        }
        if product_ids
        else {}
    )
    categories = (
        {
            row.id: row.category_name
            for row in db.query(ProductCategory)
            .filter(ProductCategory.id.in_(category_ids))
            .all()
        }
        if category_ids
        else {}
    )

    out = []
    for rule in rules:
        level = (
            LEVEL_PRODUCT
            if rule.product_id
            else LEVEL_CATEGORY
            if rule.category_id
            else LEVEL_SYSTEM
        )
        out.append(
            {
                "id": rule.id,
                "product_id": rule.product_id,
                "product_code": products.get(rule.product_id or ""),
                "category_id": rule.category_id,
                "category_name": categories.get(rule.category_id or ""),
                "mode": rule.mode,
                "value": rule.value,
                "notes": rule.notes,
                "is_active": rule.is_active,
                "level": level,
            }
        )
    return out


def _category_name(db: Session, category_id: Optional[str]) -> Optional[str]:
    if not category_id:
        return None
    row = (
        db.query(ProductCategory.category_name)
        .filter(ProductCategory.id == category_id)
        .first()
    )
    return row[0] if row else None


def effective_floor_view(
    db: Session,
    *,
    company_id: str,
    product_id: Optional[str] = None,
    category_id: Optional[str] = None,
) -> Dict[str, Any]:
    """What governs one product or one category, ready to render.

    Two facts, not one. ``own_rule`` is the rule set on this exact target and decides
    whether clearing is possible; ``effective`` is what actually applies once inheritance
    has been walked, and names its source so the reader can go and argue with the right
    policy instead of a bare number.
    """
    if bool(product_id) == bool(category_id):
        raise AppException(
            status_code=422,
            message="Name exactly one target: product_id or category_id.",
            code="floor_target_required",
        )

    if product_id:
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise AppException(
                status_code=404, message="Product not found.", code="product_not_found"
            )
        own = own_floor_rule(db, company_id=company_id, product_id=product.id)
        # Reuses the golden-set engine rather than re-deriving it: this surface must
        # agree with what a quotation line is actually judged against.
        resolved = resolve_floor(db, company_id=company_id, product=product)
        effective = None
        if resolved:
            rule = (
                db.query(PriceFloorRule)
                .filter(PriceFloorRule.id == resolved.rule_id)
                .first()
            )
            effective = {
                "rule_id": resolved.rule_id,
                "level": resolved.level,
                "mode": resolved.mode,
                "value": rule.value if rule else resolved.value,
                "amount": resolved.value,
                "source_label": product.product_code
                if resolved.level == LEVEL_PRODUCT
                else _category_name(db, resolved.category_id) or SYSTEM_SOURCE_LABEL,
            }
        return {
            "target_level": LEVEL_PRODUCT,
            "target_id": product.id,
            "target_label": product.product_code,
            "list_price": product.list_price,
            "own_rule": serialize_floor_rules(db, [own])[0] if own else None,
            "effective": effective,
        }

    category = (
        db.query(ProductCategory).filter(ProductCategory.id == category_id).first()
    )
    if not category:
        raise AppException(
            status_code=404, message="Category not found.", code="category_not_found"
        )
    own = own_floor_rule(db, company_id=company_id, category_id=category.id)
    source = resolve_category_floor_source(
        db, company_id=company_id, category_id=category.id
    )
    effective = None
    if source:
        effective = {
            "rule_id": source.rule.id,
            "level": source.level,
            "mode": source.rule.mode,
            "value": source.rule.value,
            # A percentage of no list price is not money. Printing one anyway would be
            # inventing it.
            "amount": source.rule.value
            if source.rule.mode == FLOOR_MODE_ABSOLUTE
            else None,
            "source_label": _category_name(db, source.category_id)
            or SYSTEM_SOURCE_LABEL,
        }
    return {
        "target_level": LEVEL_CATEGORY,
        "target_id": category.id,
        "target_label": category.category_name,
        "list_price": None,
        "own_rule": serialize_floor_rules(db, [own])[0] if own else None,
        "effective": effective,
    }


def _active_rules(db: Session, company_id: str) -> List[PriceFloorRule]:
    return (
        db.query(PriceFloorRule)
        .filter(
            PriceFloorRule.company_id == company_id,
            PriceFloorRule.is_active.is_(True),
        )
        .all()
    )


# ------------------------------------------------------------- rule management


def _validate_rule(payload: Dict[str, Any]) -> None:
    mode = payload.get("mode") or FLOOR_MODE_PERCENT
    if mode not in FLOOR_MODES:
        raise AppException(
            status_code=422,
            message=f"Unknown floor mode '{mode}'. Use percent or absolute.",
            code="floor_mode_invalid",
        )
    if payload.get("product_id") and payload.get("category_id"):
        raise AppException(
            status_code=422,
            message=(
                "A floor rule targets either a product or a category, not both -- the "
                "level is decided by which one you set."
            ),
            code="floor_target_ambiguous",
        )

    raw_value = payload.get("value")
    if raw_value is None:
        raise AppException(
            status_code=422,
            message="A floor rule needs a value.",
            code="floor_value_required",
        )
    value = Decimal(str(raw_value))
    if value <= 0:
        raise AppException(
            status_code=422,
            message="A floor value must be greater than zero.",
            code="floor_value_invalid",
        )
    if mode == FLOOR_MODE_PERCENT and value > 100:
        # Refused rather than clamped: a floor above list price is a policy nobody
        # means (every line would breach it), and clamping hides the typo so the admin
        # never learns their 950 should have been 95.
        raise AppException(
            status_code=422,
            message=(
                "A percentage floor cannot exceed 100% of list price. For a hard "
                "amount, use the absolute mode."
            ),
            code="floor_percent_over_hundred",
        )


def upsert_floor_rule(
    db: Session,
    *,
    company_id: str,
    payload: Dict[str, Any],
    actor_user_id: Optional[str] = None,
) -> PriceFloorRule:
    """Create or replace the rule at one level. Idempotent per target.

    Upsert rather than insert because the unique index makes one rule per target the
    invariant, and an admin editing "the Basins floor" means exactly that, not a second
    competing Basins rule.
    """
    _validate_rule(payload)

    product_id = payload.get("product_id") or None
    category_id = payload.get("category_id") or None

    existing = (
        db.query(PriceFloorRule)
        .filter(
            PriceFloorRule.company_id == company_id,
            PriceFloorRule.product_id.is_(None)
            if product_id is None
            else PriceFloorRule.product_id == product_id,
            PriceFloorRule.category_id.is_(None)
            if category_id is None
            else PriceFloorRule.category_id == category_id,
        )
        .first()
    )

    if existing:
        existing.mode = payload.get("mode") or FLOOR_MODE_PERCENT
        existing.value = Decimal(str(payload["value"]))
        if "notes" in payload:
            existing.notes = payload.get("notes")
        if "is_active" in payload:
            existing.is_active = bool(payload["is_active"])
        db.flush()
        return existing

    rule = PriceFloorRule(
        company_id=company_id,
        product_id=product_id,
        category_id=category_id,
        mode=payload.get("mode") or FLOOR_MODE_PERCENT,
        value=Decimal(str(payload["value"])),
        notes=payload.get("notes"),
        is_active=bool(payload.get("is_active", True)),
        created_by=actor_user_id,
    )
    db.add(rule)
    db.flush()
    return rule


def delete_floor_rule(db: Session, rule: PriceFloorRule) -> None:
    """Hard delete. A floor already applied to a line is stored ON that line
    (AC-E7), so removing the rule cannot rewrite quoted history."""
    db.delete(rule)
    db.flush()


def list_floor_rules(db: Session, *, company_id: str) -> List[PriceFloorRule]:
    return (
        db.query(PriceFloorRule)
        .filter(PriceFloorRule.company_id == company_id)
        .order_by(
            # Most specific first, which is the order they take effect in.
            PriceFloorRule.product_id.isnot(None).desc(),
            PriceFloorRule.category_id.isnot(None).desc(),
            PriceFloorRule.created_at.asc(),
        )
        .all()
    )


# ---------------------------------------------------------- series membership


def series_category_ids(db: Session, series_id: Optional[str]) -> Set[str]:
    """Every category the series covers, descendants included (AC-E5)."""
    if not series_id:
        return set()
    nominated = [
        row[0]
        for row in db.query(ProjectSeriesCategory.category_id)
        .filter(ProjectSeriesCategory.series_id == series_id)
        .all()
    ]
    return category_with_descendants(db, nominated)


def series_product_ids(db: Session, series_id: Optional[str]) -> Set[str]:
    """Every product nominated into the series BY NAME (S18)."""
    if not series_id:
        return set()
    return {
        row[0]
        for row in db.query(ProjectSeriesProduct.product_id)
        .filter(ProjectSeriesProduct.series_id == series_id)
        .all()
    }


def series_pricing_map(db: Session, series_id: Optional[str]) -> Dict[str, tuple]:
    """``product_id -> (selling_price, max_discount_pct)`` for a whole series, in one query.

    The pricing twin of ``series_membership``, and passed around for the same reason: a
    version runs to dozens of lines and resolving the floor per line would be a round trip
    each (AC-C7).
    """
    if not series_id:
        return {}
    return {
        row.product_id: (row.selling_price, row.max_discount_pct)
        for row in db.query(ProjectSeriesProduct)
        .filter(ProjectSeriesProduct.series_id == str(series_id))
        .all()
    }


def series_product_rows(db: Session, series_id: str) -> List[Dict[str, Any]]:
    """The series' products as the screen reads them: code, name, price, percentage, floor.

    ONE join for the whole set, not a lookup per row - the client's own series runs to 92
    products and this is drawn on every visit to the page.

    ``derived_floor`` is computed HERE rather than in the browser. It is the number a refusal
    is argued from, and the same function the pricing engine enforces with, so the figure on
    the screen and the figure that blocks a save cannot disagree.

    Ordered by product code, because a list somebody checks against a spreadsheet has to be
    in an order they can follow; insertion order is meaningless to them.
    """
    rows = (
        db.query(ProjectSeriesProduct, Product)
        .join(Product, Product.id == ProjectSeriesProduct.product_id)
        .filter(ProjectSeriesProduct.series_id == str(series_id))
        .order_by(Product.product_code)
        .all()
    )
    return [
        {
            "product_id": link.product_id,
            "product_code": product.product_code,
            "product_name": product.product_name,
            "selling_price": link.selling_price,
            "max_discount_pct": link.max_discount_pct,
            "derived_floor": series_floor(link.selling_price, link.max_discount_pct),
        }
        for link, product in rows
    ]


def set_series_product_pricing(
    db: Session,
    *,
    series: ProjectSeries,
    product_id: str,
    selling_price: Any,
    max_discount_pct: Any,
) -> Dict[str, Any]:
    """Set one product's price and percentage from the table on the series page.

    Explicit about erasure in a way the importer deliberately is not: here ``None`` MEANS
    "clear it", because the person cleared the cell. The importer treats an absent value as
    silence instead, since a sheet that states no price is not asking for one to be deleted.
    """
    link = (
        db.query(ProjectSeriesProduct)
        .filter(
            ProjectSeriesProduct.series_id == series.id,
            ProjectSeriesProduct.product_id == str(product_id),
        )
        .first()
    )
    if link is None:
        raise AppException(
            status_code=404,
            message="That product is not in this series.",
            code="series_product_not_found",
        )
    link.selling_price = selling_price
    link.max_discount_pct = max_discount_pct
    db.flush()

    product = db.query(Product).filter(Product.id == link.product_id).first()
    return {
        "product_id": link.product_id,
        "product_code": product.product_code if product else None,
        "product_name": product.product_name if product else None,
        "selling_price": link.selling_price,
        "max_discount_pct": link.max_discount_pct,
        "derived_floor": series_floor(link.selling_price, link.max_discount_pct),
    }


def remove_series_product(db: Session, *, series: ProjectSeries, product_id: str) -> bool:
    """Take one product off the series. False when it was not on it to begin with."""
    deleted = (
        db.query(ProjectSeriesProduct)
        .filter(
            ProjectSeriesProduct.series_id == series.id,
            ProjectSeriesProduct.product_id == str(product_id),
        )
        .delete(synchronize_session=False)
    )
    db.flush()
    return bool(deleted)


# ------------------------------------------------------------ product sets in a series


def is_set_in_series(db: Session, series_id: Optional[str], product_set_id: str) -> bool:
    """Is this SET priced into the series? With no series nothing is an allowlist breach.

    A set has no category, so unlike a product it is standard only by a ``series_sets`` row.
    """
    if not series_id:
        return True
    return (
        db.query(ProjectSeriesSet.series_id)
        .filter(
            ProjectSeriesSet.series_id == str(series_id),
            ProjectSeriesSet.product_set_id == str(product_set_id),
        )
        .first()
        is not None
    )


def series_set_pricing_map(db: Session, series_id: Optional[str]) -> Dict[str, tuple]:
    """``product_set_id -> (selling_price, max_discount_pct)`` for a whole series."""
    if not series_id:
        return {}
    return {
        row.product_set_id: (row.selling_price, row.max_discount_pct)
        for row in db.query(ProjectSeriesSet)
        .filter(ProjectSeriesSet.series_id == str(series_id))
        .all()
    }


def resolve_set_floor(
    db: Session,
    *,
    company_id: str,
    product_set: ProductSet,
    list_price: Optional[Decimal] = None,
    series_id: Optional[str] = None,
) -> Optional[ResolvedFloor]:
    """The floor for one SET line, or None.

    The series row wins when it states both numbers. Otherwise ONLY the company default
    (system) rule applies, against the set list price: a set is not a product and has no
    category, so product and category rules never reach it (owner answer Q4 b).
    """
    if series_id is not None:
        stated = series_set_pricing_map(db, series_id).get(product_set.id)
        if stated is not None:
            floor = series_floor(stated[0], stated[1])
            if floor is not None:
                return ResolvedFloor(
                    value=floor,
                    mode=FLOOR_MODE_ABSOLUTE,
                    level=LEVEL_SERIES,
                    rule_id=str(series_id),
                )
    system = next(
        (r for r in _active_rules(db, company_id) if not r.product_id and not r.category_id),
        None,
    )
    if system is None:
        return None
    value = _floor_from_rule(system, list_price)
    if value is None:
        return None
    return ResolvedFloor(value=value, mode=system.mode, level=LEVEL_SYSTEM, rule_id=system.id)


def _series_set_row(link: ProjectSeriesSet, product_set: Optional[ProductSet]) -> Dict[str, Any]:
    return {
        "product_set_id": link.product_set_id,
        "set_code": product_set.set_code if product_set else None,
        "set_name": product_set.name if product_set else None,
        "part_codes": [m.product.product_code for m in product_set.members]
        if product_set
        else [],
        "selling_price": link.selling_price,
        "max_discount_pct": link.max_discount_pct,
        "derived_floor": series_floor(link.selling_price, link.max_discount_pct),
    }


def series_set_rows(db: Session, series_id: str) -> List[Dict[str, Any]]:
    """The series' sets as the screen reads them, ordered by set code."""
    rows = (
        db.query(ProjectSeriesSet, ProductSet)
        .join(ProductSet, ProductSet.id == ProjectSeriesSet.product_set_id)
        .filter(ProjectSeriesSet.series_id == str(series_id))
        .order_by(ProductSet.set_code)
        .all()
    )
    return [_series_set_row(link, product_set) for link, product_set in rows]


def set_series_set_pricing(
    db: Session,
    *,
    series: ProjectSeries,
    product_set_id: str,
    selling_price: Any,
    max_discount_pct: Any,
) -> Dict[str, Any]:
    """Upsert one set's price and percentage on a series; ``None`` clears the number.

    The set is read through the company-scoped session, so another company's set is as
    absent as one that never existed.
    """
    product_set = db.query(ProductSet).filter(ProductSet.id == str(product_set_id)).first()
    if product_set is None:
        raise AppException(
            status_code=404,
            message="That product set was not found.",
            code="product_set_not_found",
        )
    link = (
        db.query(ProjectSeriesSet)
        .filter(
            ProjectSeriesSet.series_id == series.id,
            ProjectSeriesSet.product_set_id == product_set.id,
        )
        .first()
    )
    if link is None:
        link = ProjectSeriesSet(series_id=series.id, product_set_id=product_set.id)
        db.add(link)
    link.selling_price = selling_price
    link.max_discount_pct = max_discount_pct
    db.flush()
    return _series_set_row(link, product_set)


def remove_series_set(db: Session, *, series: ProjectSeries, product_set_id: str) -> bool:
    """Take one set off the series. False when it was not on it to begin with."""
    deleted = (
        db.query(ProjectSeriesSet)
        .filter(
            ProjectSeriesSet.series_id == series.id,
            ProjectSeriesSet.product_set_id == str(product_set_id),
        )
        .delete(synchronize_session=False)
    )
    db.flush()
    return bool(deleted)


def series_membership(db: Session, series_id: Optional[str]) -> SeriesMembership:
    """The whole series, expanded ONCE (S18).

    Two reads and one hierarchy walk, whether the caller then asks about one line or
    fifty-two. Without this a version's guardrail pass costs a walk per line, which is the
    shape that makes a long quotation slow for no reason at all.
    """
    return SeriesMembership(
        categories=series_category_ids(db, series_id),
        products=series_product_ids(db, series_id),
    )


def is_in_series(
    db: Session,
    *,
    series_id: Optional[str],
    product: Optional[Product],
    membership: Optional[SeriesMembership] = None,
) -> bool:
    """Is this line's product inside the nominated series?

    Answered from BOTH sides (S18): the product is in the series if it was nominated by
    name OR sits under a nominated category. The two combine rather than one overriding
    the other, because they answer different questions - "everything under Basins is fair
    game" and "these exact 142 codes are the standard range" - and a series may want
    either, both or neither.

    Direct membership is checked FIRST and needs no category at all, which also makes a
    product with a null ``category_id`` nominatable. Under category-only membership such a
    row could never be standard, however explicitly it was listed.

    An off-catalog line is never in the series (AC-E5): there is no product to look up,
    and "we quoted something that isn't in our catalogue" is exactly what the alert is
    for. With NO series nominated everything counts as standard -- a project that never
    picked a series has no allowlist to breach.

    ``membership`` lets a caller expand the series once and reuse it across a whole
    version's lines rather than re-reading it per line.
    """
    if not series_id:
        return True
    if product is None:
        return False
    resolved = membership if membership is not None else series_membership(db, series_id)
    if product.id in resolved.products:
        return True
    return product.category_id in resolved.categories


def set_series_categories(
    db: Session, *, series: ProjectSeries, category_ids: Sequence[str]
) -> None:
    """Replace the nominated set. Sent whole, not as a delta.

    Reconciled rather than deleted-and-reinserted so an unchanged nomination keeps its
    row, which keeps the audit trail readable.
    """
    wanted = {cid for cid in category_ids if cid}
    existing = {
        row.category_id: row
        for row in db.query(ProjectSeriesCategory)
        .filter(ProjectSeriesCategory.series_id == series.id)
        .all()
    }

    for category_id in wanted - set(existing):
        db.add(ProjectSeriesCategory(series_id=series.id, category_id=category_id))
    for category_id in set(existing) - wanted:
        db.delete(existing[category_id])
    db.flush()


def set_series_products(
    db: Session,
    *,
    series: ProjectSeries,
    product_ids: Sequence[str],
    pricing: Mapping[str, tuple[Any, Any]] | None = None,
) -> Dict[str, int]:
    """Replace the products nominated by name. Sent whole, not as a delta.

    Reconciled rather than deleted-and-reinserted, exactly as the category set above is
    and for the same reason: an unchanged nomination keeps its row, so the audit trail
    reads as "three added" rather than "a hundred and forty-two rewritten".

    ``pricing`` maps a product id to ``(selling_price, max_discount_pct)`` and is applied to
    kept rows as well as new ones, so re-importing a sheet with corrected prices updates them
    rather than reporting "already present" and quietly keeping the old numbers. A product
    ABSENT from the mapping keeps whatever it already had: an import that states no price for
    a product is not an instruction to erase the price somebody typed by hand.

    Returns what it did, because the importer above it has to be able to say so.
    """
    wanted = {pid for pid in product_ids if pid}
    existing = {
        row.product_id: row
        for row in db.query(ProjectSeriesProduct)
        .filter(ProjectSeriesProduct.series_id == series.id)
        .all()
    }

    added = wanted - set(existing)
    removed = set(existing) - wanted
    priced = pricing or {}
    for product_id in added:
        price, pct = priced.get(product_id, (None, None))
        db.add(
            ProjectSeriesProduct(
                series_id=series.id,
                product_id=product_id,
                selling_price=price,
                max_discount_pct=pct,
            )
        )
    for product_id in removed:
        db.delete(existing[product_id])
    for product_id in wanted & set(existing):
        if product_id in priced:
            price, pct = priced[product_id]
            existing[product_id].selling_price = price
            existing[product_id].max_discount_pct = pct
    db.flush()
    return {"added": len(added), "removed": len(removed), "kept": len(wanted & set(existing))}


def series_floor(selling_price: Any, max_discount_pct: Any) -> Optional[Decimal]:
    """The lowest a line may go when the SERIES has an opinion, or ``None``.

    ``selling_price * (1 - pct/100)``, to two places: 220 at 6% is 206.80.

    **Both numbers are required.** A series that names a price but no percentage returns
    ``None`` here and the line falls through to ``price_floor_rules`` - see AC-C4. The
    alternative, reading a missing percentage as zero, makes the floor equal the selling
    price and puts the 56 of the client's 151 codes that carry a price and no percentage in
    breach the moment anybody discounts a cent.

    One function, called by the pricing engine AND by the screen that displays the number,
    because a floor computed two ways is a refusal the user can argue with.
    """
    if selling_price is None or max_discount_pct is None:
        return None
    price = Decimal(str(selling_price))
    pct = Decimal(str(max_discount_pct))
    if price < 0 or pct < 0 or pct > 100:
        return None
    floor = price * (Decimal(1) - pct / Decimal(100))
    return floor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


SERIES_IMPORT_MODES = ("append", "replace")


@dataclass(frozen=True)
class _SeriesRow:
    """A bare code, wrapped so the importer has ONE shape to walk.

    Deliberately local and structural rather than importing the sheet reader's
    ``SeriesSheetRow``: this service does not care that a row came from a spreadsheet, only
    that it has a code and may carry a price. Anything with a ``.code`` is accepted.
    """

    code: str
    selling_price: Any = None
    max_discount_pct: Any = None

# The SQL twin of ``normalize_code``. Written once here so the WHERE clause and the Python
# comparison cannot drift: a code that normalises one way in memory and another in the
# database would report itself unmatched while sitting in the catalogue.
_NORM_PRODUCT_CODE_SQL = "lower(regexp_replace(product_code, '[-\\s]', '', 'g'))"


def resolve_product_codes(
    db: Session, codes: Sequence[str]
) -> tuple[Dict[str, List[Product]], List[str]]:
    """Codes as the catalogue understands them, and the ones it does not carry.

    Returns ``(matched, unmatched)`` where ``matched`` maps the NORMALISED code to every
    product row carrying it and ``unmatched`` holds the caller's own spelling, in the order
    it was given, deduplicated.

    One query for the whole list rather than one per code: a 151-cell sheet is a single
    round trip. Company scope is the ORM's, so a series in one company can never nominate
    another company's identically-coded row.

    A normalised code matching MORE THAN ONE product is not an error. `CWB-242` and
    `CWB 242` are the same code to the person who wrote the sheet, so both rows are
    nominated; refusing would leave the admin unable to express what they plainly meant.
    """
    seen: Dict[str, str] = {}
    for raw in codes:
        normalised = normalize_code(raw)
        if not normalised:
            continue
        seen.setdefault(normalised, (raw or "").strip())

    if not seen:
        return {}, []

    rows = (
        db.query(Product)
        .filter(text(f"{_NORM_PRODUCT_CODE_SQL} = ANY(:codes)").bindparams(codes=list(seen)))
        .all()
    )

    matched: Dict[str, List[Product]] = {}
    for row in rows:
        matched.setdefault(normalize_code(row.product_code), []).append(row)

    unmatched = [original for key, original in seen.items() if key not in matched]
    return matched, unmatched


def apply_series_product_codes(
    db: Session,
    *,
    series: ProjectSeries,
    codes: Sequence[str],
    mode: str = "append",
) -> Dict[str, Any]:
    """Load a list of product CODES onto a series, and report every code that missed (S18).

    This is the whole of the client's definition of "standard": a list of codes off a
    sheet, not a set of groups. Two modes, because both are real - ``append`` adds this
    year's range to an existing series, ``replace`` makes the series say exactly what the
    sheet says and nothing else.

    **The unmatched list is the point, not a footnote.** Their own template quotes base
    codes the catalogue only stocks as suffixed variants (`CWC1009-RL` against a stocked
    `CWC1009-SC`), so a third of a real sheet can miss. A loader that dropped those
    silently would turn "your sheet and our catalogue disagree here, and here" into a
    smaller number nobody could interrogate.

    An empty list is REFUSED rather than obeyed: in ``replace`` mode it would wipe the
    series while reporting a cheerful zero.

    ``codes`` accepts plain strings OR ``SeriesSheetRow``s carrying a price and a maximum
    discount (T2). One parameter rather than two, because a separate ``rows=`` would be a
    second way to say the same thing and the two could disagree. A row states a price for
    EVERY product its code resolves to - `CWB-242` and `CWB 242` are one code to whoever
    wrote the sheet - and where a code appears twice, the last row wins, which is the only
    reading under which re-importing a corrected sheet does what the admin expects.
    """
    if mode not in SERIES_IMPORT_MODES:
        raise AppException(
            status_code=422,
            message=f"Unknown mode '{mode}'. Use append or replace.",
            code="series_import_mode_invalid",
        )

    # Strings and priced rows are both accepted; normalise to rows once, here.
    sheet_rows = [
        entry if hasattr(entry, "code") else _SeriesRow(code=str(entry)) for entry in codes
    ]
    submitted = len(sheet_rows)
    matched, unmatched = resolve_product_codes(db, [row.code for row in sheet_rows])
    if not matched and not unmatched:
        raise AppException(
            status_code=422,
            message=(
                "No product codes were found in what you sent. Paste one code per line, "
                "or upload a sheet with a PRODUCT CODE column."
            ),
            code="series_import_empty",
        )

    resolved_ids = {product.id for rows in matched.values() for product in rows}
    before = series_product_ids(db, series.id)
    wanted = resolved_ids if mode == "replace" else before | resolved_ids

    # What each product should now cost. Only rows that actually state something appear, so a
    # code-only paste never blanks a price an admin typed on this page.
    pricing: Dict[str, tuple[Any, Any]] = {}
    for row in sheet_rows:
        if row.selling_price is None and row.max_discount_pct is None:
            continue
        for product in matched.get(normalize_code(row.code), []):
            pricing[product.id] = (row.selling_price, row.max_discount_pct)

    outcome = set_series_products(
        db, series=series, product_ids=wanted, pricing=pricing
    )

    return {
        "series_id": series.id,
        "series_name": series.name,
        "mode": mode,
        "submitted": submitted,
        "unique_codes": len(matched) + len(unmatched),
        "matched_codes": len(matched),
        "added": outcome["added"],
        # Counted against what was ALREADY on the series, so "you have imported this
        # sheet before" is distinguishable from "it added nothing because nothing matched".
        "already_present": len(resolved_ids & before),
        "removed": outcome["removed"],
        "product_count": len(wanted),
        "unmatched_codes": unmatched,
    }
