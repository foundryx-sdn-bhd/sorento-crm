"""Product alternatives: the code rule, the stored links and the one lookup.

A link (`product_alternatives`) says "use `alternative_product_id` instead of `product_id`",
same company. Two writers:

* the code rule (`reconcile_new_alternatives`, source `auto_new`): product Y is the
  alternative of product X when removing exactly ONE whole marker segment (`NEW`, `DIY`, `N`
  or `PP`) from Y's code gives X's code, or when replacing exactly one whole `PP` segment of
  Y with `NL` gives X's code (split on `-`, case-insensitive, whitespace around each segment
  trimmed). It is a data rule on product codes, run from the AutoCount ingest, the product
  API create/update, the Excel import and the one-off backfill; it never reads user text.
* a person on the product page (source `manual`). Any hand edit sets
  `products.alternatives_manual`, and the rule never touches that product's links again
  until Reset to auto.

One reader for every chatbot domain: `alternatives_for`.
Card: documentation/plans/chatbot/CARD-product-alternatives-08oct.md.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
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
# Whole segments that mark a code as an alternative of the same code without them.
_MARKERS = (_NEW, "DIY", "N", "PP")
_PP, _NL = "PP", "NL"
# SQL form of "has a marker segment", for the backfill's candidate filter.
_MARKER_SEGMENT_RE = r"(^|-)(" + "|".join(_MARKERS) + r")($|-)"


_DASH_RE = re.compile(r"\s*-\s*")


def normalize_code(code: Optional[str]) -> str:
    """Upper-cased, whitespace removed around every `-`, spaces trimmed at the ends:
    `SRT1000-CR - NEW` and `srt1000-cr-new` compare equal. The exact Python twin of
    `_norm_sql` (regexp `\\s*-\\s*`, then `btrim`, which trims spaces only)."""
    if not code:
        return ""
    return _DASH_RE.sub("-", code.upper()).strip(" ")


def _norm_sql(column):
    """The SQL normaliser. `prod_alt_0001_tables` indexes exactly this expression per
    company (`ix_products_company_norm_code`), so the rule's per-product lookup is an
    index probe, not a catalogue scan; keep the two in step."""
    return func.btrim(func.upper(func.regexp_replace(column, r"\s*-\s*", "-", "g")))


def new_base_codes(code: Optional[str]) -> set[str]:
    """The codes this code is the alternative of: one whole marker segment (`NEW`, `DIY`,
    `N`, `PP`) removed, or one whole `PP` segment replaced by `NL`."""
    segments = normalize_code(code).split("-") if code else []
    bases = set()
    for index, segment in enumerate(segments):
        if segment not in _MARKERS:
            continue
        rest = segments[:index] + segments[index + 1 :]
        base = "-".join(rest)
        if rest and base.strip("-"):
            bases.add(base)
        if segment == _PP:
            bases.add("-".join(segments[:index] + [_NL] + segments[index + 1 :]))
    return bases


def _new_codes_for(code: str) -> set[str]:
    """The codes that would be this code's alternatives: a marker inserted as one whole
    segment at any position, or an `NL` segment turned into `PP` (the inverse of
    `new_base_codes`)."""
    segments = normalize_code(code).split("-")
    if not code or not any(segments):
        return set()
    out = {
        "-".join(segments[:index] + [marker] + segments[index:])
        for marker in _MARKERS
        for index in range(len(segments) + 1)
    }
    for index, segment in enumerate(segments):
        if segment == _NL:
            out.add("-".join(segments[:index] + [_PP] + segments[index + 1 :]))
    return out


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
    """One-off pass over every product whose code has a marker segment (`NEW`, `DIY`, `N`, `PP`).

    ``apply=False`` (the default) reports what it would add and writes nothing."""
    candidates = (
        db.query(Product)
        .filter(_norm_sql(Product.product_code).op("~")(_MARKER_SEGMENT_RE))
        .order_by(Product.product_code)
        .all()
    )
    def _pairs() -> set[tuple[str, str]]:
        return set(
            db.query(ProductAlternative.product_id, ProductAlternative.alternative_product_id).all()
        )

    before = _pairs()
    savepoint = db.begin_nested()
    added = sum(reconcile_new_alternatives(db, p) for p in candidates)
    codes = {p.id: p.product_code for p in candidates}
    new_pairs = sorted(_pairs() - before)
    missing = {pid for pair in new_pairs for pid in pair if pid not in codes}
    if missing:
        codes.update(dict(db.query(Product.id, Product.product_code).filter(Product.id.in_(missing))))
    pairs = sorted((codes.get(a) or a, codes.get(b) or b) for a, b in new_pairs)
    if apply:
        savepoint.commit()
        return {"scanned": len(candidates), "added": added, "pairs": pairs}
    savepoint.rollback()
    return {"scanned": len(candidates), "would_add": added, "pairs": pairs}


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
    if not alternative.is_active:
        raise handle_validation_error(f"{alternative.product_code} is not active.")
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
    try:
        db.commit()
    except IntegrityError:
        # Two adds of the same pair racing: the unique pair index is the arbiter.
        db.rollback()
        raise handle_conflict(f"{alternative.product_code} is already an alternative.")
    db.refresh(link)
    return _row(link, alternative)


def get_link(db: Session, link_id: str) -> Optional[ProductAlternative]:
    return db.query(ProductAlternative).filter(ProductAlternative.id == link_id).first()


def unlink(db: Session, link_id: str) -> None:
    """Hard delete one link; the product it hangs off becomes manual so the rule never
    puts it back."""
    link = get_link(db, link_id)
    if link is None:
        # Already gone (a Reset to auto on the same product committed first): the
        # outcome the reader asked for holds, so the deferred action ends quietly. A
        # link that never existed is refused at park time (`pending_actions`).
        return
    product = db.get(Product, link.product_id)
    if product is not None:
        product.alternatives_manual = True
    db.delete(link)
    db.commit()


def reset_auto(db: Session, product_id: str) -> dict:
    """Back to the rule: every link of this product goes, the manual mark clears, and the
    code rule derives it again."""
    product = _product_or_404(db, product_id)
    # Row by row, not a bulk delete: each removed link goes through the ORM so the
    # `__audit_track__` listeners record it.
    for link in db.query(ProductAlternative).filter(ProductAlternative.product_id == product.id):
        db.delete(link)
    product.alternatives_manual = False
    db.flush()
    reconcile_new_alternatives(db, product)
    db.commit()
    return list_for_product(db, product_id)
