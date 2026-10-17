"""Collections: CRUD and the resolver that turns one into tiles.

Resolution is four steps, in this order, and the order is the point:

  1. **Company scope** narrows the candidate products. It runs FIRST, via the
     ORM filter, so a rule can never match another company's catalogue no matter
     what it says (AC-F8).
  2. **The rule** is evaluated by the SHARED ``app/rule_engine`` over registered
     product facts - not a bespoke filter (AC-F3).
  3. **Set algebra**: rule union pins minus exclusions, ordered (AC-F2). Pure,
     and tested exhaustively in ``collection_membership.py``.
  4. **Viewer resolution** turns members into tiles, deciding per reader what
     price (if any) appears. The document never held one (AC-G1).

Discontinued and inactive products are dropped before tiles are built (AC-G4):
a dead tile in a catalogue is an order someone cannot fulfil.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional, Sequence

from sqlalchemy.orm import Session, joinedload

from app.models.dealer_kit import Collection
from app.models.product import Product
from app.rule_engine.evaluator import collect_fact_keys, evaluate
from app.services.dealer_kit.collection_membership import assemble_members
from app.services.dealer_kit.product_facts import product_facts
from app.services.dealer_kit import pricing, product_images
from app.services.dealer_kit.viewer import ANONYMOUS, ViewerContext
from app.services.error_handler import AppException

PAGE = "page"
LIBRARY = "library"


def get_collection(db: Session, collection_id: str) -> Collection:
    row = db.query(Collection).filter(Collection.id == collection_id).first()
    if row is None:
        # Company scope already ran, so another company's collection and a
        # non-existent one are the same answer.
        raise AppException(status_code=404, message="Collection not found")
    return row


def _validate_conditions(conditions: Optional[dict]) -> None:
    """Refuse only bad number values (non-finite or unreadable) in a number fact's rule.
    Blank or empty shapes the builder has always saved are left alone."""
    if not conditions or not conditions.get("rules"):
        return
    from app.rule_engine.schemas import _blank, _numeric
    from app.rule_engine.registry import fact_map
    from app.services.dealer_kit.product_facts import SOURCE, register_product_facts

    register_product_facts()
    facts = fact_map([SOURCE])

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        # Only a group has children; a condition that carries "rules" is still a condition.
        if node.get("kind") == "group" or ("kind" not in node and "fact" not in node):
            for rule in node.get("rules") or []:
                walk(rule)
            return
        fact = facts.get(node.get("fact"))
        if fact is None or fact.type != "number" or node.get("valueKind") == "fact":
            return
        value = node.get("value")
        members = value if isinstance(value, list) else [value]
        for member in members:
            if not _blank(member) and not _numeric(member):
                raise AppException(
                    status_code=422, message=f"'{fact.label}' needs a numeric value."
                )

    walk(conditions)


def create_collection(
    db: Session,
    *,
    scope: str = PAGE,
    page_id: Optional[str] = None,
    name: Optional[str] = None,
    conditions: Optional[dict] = None,
    pinned_product_ids: Optional[Sequence[str]] = None,
    excluded_product_ids: Optional[Sequence[str]] = None,
    manual_order: Optional[Sequence[str]] = None,
    user_id: Optional[str] = None,
) -> Collection:
    if scope not in (PAGE, LIBRARY):
        raise AppException(status_code=422, message=f"Unknown collection scope '{scope}'")
    if scope == PAGE and not page_id:
        raise AppException(
            status_code=422, message="A page-scoped collection must belong to a page"
        )
    if scope == LIBRARY and not (name or "").strip():
        raise AppException(status_code=422, message="A reusable collection needs a name")

    _validate_conditions(conditions)
    row = Collection(
        scope=scope,
        page_id=page_id,
        name=(name or None),
        conditions_json=conditions,
        pinned_product_ids=list(pinned_product_ids or []),
        excluded_product_ids=list(excluded_product_ids or []),
        manual_order=list(manual_order or []),
        created_by=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_collection(db: Session, collection_id: str, **changes) -> Collection:
    row = get_collection(db, collection_id)
    if "conditions_json" in changes:
        _validate_conditions(changes["conditions_json"])
    for field in (
        "name",
        "conditions_json",
        "pinned_product_ids",
        "excluded_product_ids",
        "manual_order",
    ):
        if field in changes:
            setattr(row, field, changes[field])
    db.commit()
    db.refresh(row)
    return row


def save_as_library(db: Session, collection_id: str, name: str) -> Collection:
    """Promote a page-scoped collection into the reusable library (AC-F5).

    The page keeps pointing at the SAME row, so promoting does not orphan the
    block that was using it - which is what a Designer expects when they name
    something they already built.
    """
    if not (name or "").strip():
        raise AppException(status_code=422, message="A reusable collection needs a name")

    row = get_collection(db, collection_id)
    row.scope = LIBRARY
    row.name = name.strip()
    db.commit()
    db.refresh(row)
    return row


def list_library(db: Session) -> list[Collection]:
    """Only reusable collections. Page-scoped ones are an editor implementation
    detail and would be noise in a library list (AC-F4)."""
    return (
        db.query(Collection)
        .filter(Collection.scope == LIBRARY)
        .order_by(Collection.name)
        .all()
    )


def delete_collection(db: Session, collection_id: str) -> None:
    db.delete(get_collection(db, collection_id))
    db.commit()


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def _sellable_products(db: Session) -> list[Product]:
    """Candidates, company-scoped by the ORM filter and already excluding what
    can never be sold. Filtering in SQL rather than after the rule keeps a
    discontinued product from ever being a member."""
    return (
        db.query(Product)
        .options(joinedload(Product.category), joinedload(Product.brand))
        .filter(Product.is_active.is_(True), Product.is_discontinued.is_(False))
        .order_by(Product.product_code)
        .all()
    )


def _sellable_by_ids(db: Session, product_ids: Iterable[str]) -> list[Product]:
    """Just these products, with the same sellable filter the scan applies.

    The filter is repeated rather than skipped on purpose: on the scan path a
    pin pointing at something discontinued dropped out because it was never in
    the candidate set, and the cheap path has to keep that guarantee or an
    unbuyable product reaches a public page.
    """
    ids = [product_id for product_id in product_ids if product_id]
    if not ids:
        return []
    return (
        db.query(Product)
        .options(joinedload(Product.category), joinedload(Product.brand))
        .filter(Product.id.in_(ids))
        .filter(Product.is_active.is_(True), Product.is_discontinued.is_(False))
        .order_by(Product.product_code)
        .all()
    )


def _matched_ids(db: Session, conditions: Optional[dict], candidates: Iterable[Product]) -> list[str]:
    if not conditions or not conditions.get("rules"):
        # An empty rule matches nothing here, NOT everything. The engine's own
        # convention is that an empty tree is unconditional, which is right for
        # "may this promotion run" but catastrophic for "which products are in
        # this collection" - it would silently put the entire catalogue on the
        # page. A collection with no rule has only its pins.
        return []

    # Resolve only the facts the rule actually reads: a computed fact should not
    # be paid for on every product when no condition mentions it.
    only = collect_fact_keys(conditions)
    return [
        product.id
        for product in candidates
        if evaluate(conditions, product_facts(product, db, only_keys=only))
    ]


def _money(value: Optional[Decimal], currency: str) -> Optional[str]:
    if value is None:
        return None
    return f"{currency} {Decimal(value):,.2f}"


def resolve_members(
    db: Session,
    collection: Collection,
    candidates: Optional[list[Product]] = None,
) -> list[Product]:
    """The ordered member products of a collection.

    ``candidates`` lets a caller resolving SEVERAL collections load the product
    set once. Without it, listing twenty collections meant twenty full catalogue
    scans in one request - the rule engine is a Python evaluator, so the
    candidate load is the expensive part and it is identical for every
    collection in the same company scope.
    """
    conditions = collection.conditions_json
    has_rule = bool(conditions and conditions.get("rules"))

    if candidates is None:
        # A hand-picked collection needs its pins and nothing else. Scanning the
        # catalogue to answer "show these four" cost a full product load - with
        # category and brand joined - on every public page view, and the live
        # catalogue holds over seventeen thousand sellable products. A rule has
        # no cheaper path: it is a Python evaluator and must see the candidates.
        candidates = (
            _sellable_products(db)
            if has_rule
            else _sellable_by_ids(db, collection.pinned_product_ids or [])
        )

    by_id = {product.id: product for product in candidates}

    member_ids = assemble_members(
        matched=_matched_ids(db, conditions, candidates),
        pinned=collection.pinned_product_ids,
        excluded=collection.excluded_product_ids,
        manual_order=collection.manual_order,
    )

    # A pinned id that is discontinued, deleted or another company's simply is
    # not in `by_id`, so it drops out here rather than becoming a broken tile.
    return [by_id[member_id] for member_id in member_ids if member_id in by_id]


def sellable_products(db: Session) -> list[Product]:
    """Public handle on the candidate set, for callers resolving several
    collections in one request."""
    return _sellable_products(db)


def promotion_for(db: Session, collection: Collection) -> Optional[str]:
    """Which promotion prices this collection's tiles, if any.

    The PAGE carries the binding, never the collection: which offer a brochure
    quotes is one editorial decision made once (PLAN D5), and a collection
    reused on two pages must price differently on each. A library collection
    belongs to no page and therefore has no offer, which is list prices - a
    normal state, not a defect (D6).
    """
    if not collection.page_id:
        return None

    from app.models.dealer_kit import Page

    return db.query(Page.promotion_id).filter(Page.id == collection.page_id).scalar()


def resolve_tiles(
    db: Session,
    collection: Collection,
    viewer: ViewerContext = ANONYMOUS,
    candidates: Optional[list[Product]] = None,
    promotion_id: Optional[str] = None,
) -> list[dict]:
    """Members as tiles, with prices and photos decided for THIS viewer.

    Every figure comes from ``resolve_prices`` (ADR 0008). This module reads no
    price column of its own and does no money arithmetic: it formats what it is
    handed, once, at the edge.
    """
    return resolve_tiles_bulk(db, [collection], viewer, candidates, promotion_id)[
        collection.id
    ]


def resolve_tiles_bulk(
    db: Session,
    collections: Sequence[Collection],
    viewer: ViewerContext = ANONYMOUS,
    candidates: Optional[list[Product]] = None,
    promotion_id: Optional[str] = None,
) -> dict[str, list[dict]]:
    """The same thing for SEVERAL collections, in a fixed number of queries.

    A brochure seeded from the printed flyer carries one collection per printed
    row - the A3 flyer produces 341 of them - and resolving each on its own cost
    two round trips for its photos and its prices. That is not a slow query, it
    is seven hundred fast ones, and it was two thirds of the time a reader spent
    waiting for the page.

    Members are decided per collection (pure Python once the candidate set is
    loaded), then the UNION of every member is priced and photographed ONCE and
    the tiles are assembled from those two maps. Three queries for the whole
    document, whether it holds one row or four hundred.
    """
    if not collections:
        return {}

    if candidates is None:
        candidates = _shared_candidates(db, collections)

    members_by_collection = {
        collection.id: resolve_members(db, collection, candidates)
        for collection in collections
    }

    # De-duped, because the same product legitimately appears on several rows.
    union: list[Product] = []
    seen: set[str] = set()
    for members in members_by_collection.values():
        for product in members:
            if product.id not in seen:
                seen.add(product.id)
                union.append(product)

    images = product_images.primary_image_urls(db, union, viewer)
    prices = pricing.resolve_prices(db, union, viewer, promotion_id)

    return {
        collection_id: [_tile(product, prices[product.id], images.get(product.id)) for product in members]
        for collection_id, members in members_by_collection.items()
    }


def _shared_candidates(db: Session, collections: Sequence[Collection]) -> list[Product]:
    """One candidate set covering every collection in the batch.

    A rule is a Python evaluator and must see the whole sellable catalogue, so
    one rule anywhere in the batch means the full load - paid once for all of
    them, which is the reason this function exists.

    With no rule anywhere, nobody needs it. A seeded brochure is hundreds of
    hand-picked rows, and loading seventeen thousand products to answer "show
    these four, four hundred times over" is the expensive mistake `resolve_
    members` already avoids one collection at a time. The union of the pins is
    the same answer for a single query instead of one per collection.
    """
    if any(
        bool(collection.conditions_json and collection.conditions_json.get("rules"))
        for collection in collections
    ):
        return _sellable_products(db)

    pinned: list[str] = []
    seen: set[str] = set()
    for collection in collections:
        for product_id in collection.pinned_product_ids or []:
            if product_id not in seen:
                seen.add(product_id)
                pinned.append(product_id)

    return _sellable_by_ids(db, pinned) if pinned else []


def _tile(product: Product, price, image_url: Optional[str]) -> dict:
    currency = price.currency
    return {
        "product_id": product.id,
        "product_code": product.product_code,
        "product_name": product.product_name,
        "price": _money(price.list_price, currency),
        # Reported BESIDE the list price, not instead of it: the tile strikes
        # the list price through, and a tile handed only the offer could not
        # show what the reader is saving. None when no offer applies to THIS
        # reader, which is also how a promotion they may not see reaches them:
        # not at all (AC-G7). The promotion's id is deliberately not on the tile
        # - it would name an offer to somebody who cannot have it, and a uuid
        # has no business on a screen.
        "offer_price": _money(price.offer_price, currency),
        # Absent unless the document says show it AND the viewer may see it.
        # Both gates are ANDed inside `resolve_prices`, and the losing case
        # omits the number entirely rather than sending it to be hidden
        # (AC-G6, AC-G7).
        "invoice_price": _money(price.invoice_price, currency),
        # Photos are viewer-gated exactly as prices are: trade imagery is tagged
        # `dealer` and must not reach a consumer. Absent when there is no
        # permitted photo, so the tile shows its no-image state rather than a
        # broken one.
        "image_url": image_url,
        "dimensions": _dimensions(product),
        "badges": [],
    }


def _dimensions(product: Product) -> Optional[str]:
    """One formatting rule, shared with the price tag block.

    Delegated rather than duplicated: a tile and a tag print the same product,
    and two copies of "800 x 500 x 220 mm" would drift the first time either was
    touched.
    """
    from app.services.dealer_kit.tag_data_service import format_dimensions_mm

    return format_dimensions_mm(
        product.dimensions_length,
        product.dimensions_width,
        product.dimensions_height,
    )
