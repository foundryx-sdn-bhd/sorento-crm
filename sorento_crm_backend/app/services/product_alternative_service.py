"""Product alternatives: the -NEW code rule, the stored links and the one lookup.

A link (`product_alternatives`) says "use `alternative_product_id` instead of `product_id`",
same company. Two writers:

* the -NEW rule (`reconcile_new_alternatives`, source `auto_new`): product Y is the
  alternative of product X when removing exactly ONE whole `NEW` segment from Y's code gives
  X's code (split on `-`, case-insensitive, whitespace around each segment trimmed). It is a
  data rule on product codes, run from the AutoCount ingest, the product API create/update,
  the Excel import and the one-off backfill; it never reads user text.
* a person on the product page (source `manual`). Any hand edit sets
  `products.alternatives_manual`, and the rule never touches that product's links again
  until Reset to auto.

One reader for every chatbot domain: `alternatives_for`.
Card: documentation/plans/chatbot/CARD-product-alternatives-08oct.md.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.product import Product, ProductAlternative, chat_searchable_products
from app.services.error_handler import (
    handle_conflict,
    handle_not_found,
    handle_validation_error,
)

logger = logging.getLogger(__name__)

SOURCE_AUTO_NEW = "auto_new"
SOURCE_MANUAL = "manual"

_NEW = "NEW"


def normalize_code(code: Optional[str]) -> str:
    """Upper-cased, whitespace trimmed around every `-` segment: `SRT1000-CR - NEW` and
    `srt1000-cr-new` compare equal. Matches `_norm_sql` below."""
    if not code:
        return ""
    return "-".join(segment.strip() for segment in code.upper().split("-")).strip()


def _norm_sql(column):
    return func.btrim(func.upper(func.regexp_replace(column, r"\s*-\s*", "-", "g")))


def new_base_codes(code: Optional[str]) -> set[str]:
    """The codes this code is the -NEW alternative of: one whole `NEW` segment removed."""
    segments = normalize_code(code).split("-") if code else []
    bases = set()
    for index, segment in enumerate(segments):
        if segment != _NEW:
            continue
        rest = segments[:index] + segments[index + 1 :]
        base = "-".join(rest)
        if rest and base.strip("-"):
            bases.add(base)
    return bases


def _new_codes_for(code: str) -> set[str]:
    """The codes that would be this code's -NEW alternatives: `NEW` inserted as one
    whole segment at any position (the inverse of `new_base_codes`)."""
    segments = normalize_code(code).split("-")
    if not code or not any(segments):
        return set()
    return {
        "-".join(segments[:index] + [_NEW] + segments[index:])
        for index in range(len(segments) + 1)
    }


def _same_company_by_codes(db: Session, product: Product, codes: set[str]) -> list[Product]:
    if not codes:
        return []
    return (
        db.query(Product)
        .filter(
            Product.company_id == product.company_id,
            Product.id != product.id,
            _norm_sql(Product.product_code).in_(sorted(codes)),
        )
        .all()
    )


def reconcile_new_alternatives(db: Session, product: Product) -> int:
    """Make the -NEW rule's links for ``product`` correct, on either side of a pair.

    Adds the missing `auto_new` links and drops an `auto_new` link the codes no longer
    support (a rename), never touching a `manual` link or a product marked
    `alternatives_manual`. Idempotent. Flushes, never commits. Returns links added."""
    if product is None or not product.product_code:
        return 0
    added = 0

    # This product as the alternative (X-NEW): its base(s).
    bases = _same_company_by_codes(db, product, new_base_codes(product.product_code))
    base_ids = {b.id for b in bases}
    for base in bases:
        if base.alternatives_manual:
            continue
        added += _ensure_link(db, base, product)
    for link in (
        db.query(ProductAlternative)
        .filter(
            ProductAlternative.alternative_product_id == product.id,
            ProductAlternative.source == SOURCE_AUTO_NEW,
        )
        .all()
    ):
        if link.product_id in base_ids:
            continue
        owner = db.get(Product, link.product_id)
        if owner is not None and not owner.alternatives_manual:
            db.delete(link)

    # This product as the base (X): its -NEW code(s).
    if not product.alternatives_manual:
        news = _same_company_by_codes(db, product, _new_codes_for(product.product_code))
        new_ids = {n.id for n in news}
        for new in news:
            added += _ensure_link(db, product, new)
        for link in (
            db.query(ProductAlternative)
            .filter(
                ProductAlternative.product_id == product.id,
                ProductAlternative.source == SOURCE_AUTO_NEW,
            )
            .all()
        ):
            if link.alternative_product_id not in new_ids:
                db.delete(link)
    db.flush()
    return added


def _ensure_link(db: Session, product: Product, alternative: Product) -> int:
    exists = (
        db.query(ProductAlternative.id)
        .filter(
            ProductAlternative.product_id == product.id,
            ProductAlternative.alternative_product_id == alternative.id,
        )
        .first()
    )
    if exists:
        return 0
    db.add(
        ProductAlternative(
            product_id=product.id,
            alternative_product_id=alternative.id,
            source=SOURCE_AUTO_NEW,
            company_id=product.company_id,
        )
    )
    db.flush()
    return 1


def reconcile_new_alternatives_safely(db: Session, product_id: str) -> None:
    """Post-write side effect for the write paths: never raises (a link that failed to
    derive must not fail the product write it rides on), runs in its own savepoint."""
    try:
        with db.begin_nested():
            product = db.get(Product, product_id)
            if product is not None:
                reconcile_new_alternatives(db, product)
    except Exception:
        logger.warning("product alternatives reconcile failed for %s", product_id, exc_info=True)


def backfill_new_alternatives(db: Session, *, apply: bool = False) -> dict:
    """One-off pass over every product whose code has a `NEW` segment.

    ``apply=False`` (the default) reports what it would add and writes nothing."""
    candidates = (
        db.query(Product)
        .filter(_norm_sql(Product.product_code).op("~")(r"(^|-)NEW($|-)"))
        .order_by(Product.product_code)
        .all()
    )
    savepoint = db.begin_nested()
    added = sum(reconcile_new_alternatives(db, p) for p in candidates)
    if apply:
        savepoint.commit()
        return {"scanned": len(candidates), "added": added}
    savepoint.rollback()
    return {"scanned": len(candidates), "would_add": added}


def alternatives_for(db: Session, product_ids: Iterable[str]) -> dict[str, list[Product]]:
    """The one lookup every chatbot domain reads: ``{product_id: [alternative Product]}``.

    One hop (an alternative's own alternatives are not followed), active chat-searchable
    alternatives only, ordered by code. Company-scoped like every product read."""
    ids = [str(pid) for pid in product_ids if pid]
    if not ids:
        return {}
    rows = (
        db.query(ProductAlternative.product_id, Product)
        .join(Product, Product.id == ProductAlternative.alternative_product_id)
        .filter(
            ProductAlternative.product_id.in_(ids),
            Product.is_active.is_(True),
            chat_searchable_products(),
        )
        .order_by(Product.product_code, Product.id)
        .all()
    )
    out: dict[str, list[Product]] = {}
    for product_id, alternative in rows:
        out.setdefault(str(product_id), []).append(alternative)
    return out


# --------------------------------------------------------------------------- product page


def _product_or_404(db: Session, product_id: str) -> Product:
    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        raise handle_not_found("Product", product_id)
    return product


def _row(link: ProductAlternative, alternative: Product) -> dict:
    return {
        "id": link.id,
        "alternative_product_id": alternative.id,
        "product_code": alternative.product_code,
        "product_name": alternative.product_name,
        "source": link.source,
        "is_discontinued": bool(alternative.is_discontinued),
    }


def list_for_product(db: Session, product_id: str) -> dict:
    product = _product_or_404(db, product_id)
    rows = (
        db.query(ProductAlternative, Product)
        .join(Product, Product.id == ProductAlternative.alternative_product_id)
        .filter(ProductAlternative.product_id == product.id)
        .order_by(Product.product_code, Product.id)
        .all()
    )
    return {
        "alternatives_manual": bool(product.alternatives_manual),
        "alternatives": [_row(link, alternative) for link, alternative in rows],
    }


def add_manual(db: Session, product_id: str, alternative_product_id: str, user_id: Optional[str]) -> dict:
    product = _product_or_404(db, product_id)
    if str(alternative_product_id) == str(product.id):
        raise handle_validation_error("A product cannot be its own alternative.")
    alternative = (
        db.query(Product)
        .filter(
            Product.id == alternative_product_id,
            Product.company_id == product.company_id,
        )
        .first()
    )
    if alternative is None:
        raise handle_not_found("Product", alternative_product_id)
    exists = (
        db.query(ProductAlternative.id)
        .filter(
            ProductAlternative.product_id == product.id,
            ProductAlternative.alternative_product_id == alternative.id,
        )
        .first()
    )
    if exists:
        raise handle_conflict(f"{alternative.product_code} is already an alternative.")
    link = ProductAlternative(
        product_id=product.id,
        alternative_product_id=alternative.id,
        source=SOURCE_MANUAL,
        company_id=product.company_id,
        created_by=user_id,
    )
    db.add(link)
    product.alternatives_manual = True
    db.commit()
    db.refresh(link)
    return _row(link, alternative)


def get_link(db: Session, link_id: str) -> Optional[ProductAlternative]:
    return db.query(ProductAlternative).filter(ProductAlternative.id == link_id).first()


def unlink(db: Session, link_id: str) -> None:
    """Hard delete one link; the product it hangs off becomes manual so the rule never
    puts it back."""
    link = get_link(db, link_id)
    if link is None:
        raise handle_not_found("Product alternative", link_id)
    product = db.get(Product, link.product_id)
    if product is not None:
        product.alternatives_manual = True
    db.delete(link)
    db.commit()


def reset_auto(db: Session, product_id: str) -> dict:
    """Back to the rule: every link of this product goes, the manual mark clears, and the
    -NEW rule derives it again."""
    product = _product_or_404(db, product_id)
    db.query(ProductAlternative).filter(ProductAlternative.product_id == product.id).delete(
        synchronize_session=False
    )
    product.alternatives_manual = False
    db.flush()
    reconcile_new_alternatives(db, product)
    db.commit()
    return list_for_product(db, product_id)
