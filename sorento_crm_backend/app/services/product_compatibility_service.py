"""Seat cover <-> WC compatibility: the links and the one lookup.

A link (`product_compatibility`) says "seat cover X fits WC SKU Y", same company, one SKU
to one SKU. Owner ruling 12 Oct: match only what the flyer prints, fabricate nothing. So
there is no model-wide link and no inheritance: a WC SKU has a cover only when a link names
it, and a WC SKU with no link has no answer.

Two writers:

* the one-off flyer script (source `flyer`): `scripts/oneoff/link_seat_cover_flyer.py`;
* a person on the product page's Compatible section, Variants tab (source `manual`), which
  is where the links are kept from then on.

A `-SC` SKU (`SRTWC8383-SC`) is a seat cover, never a WC (owner ruling 12 Oct, Q2).

Readers: `covers_for` / `wcs_for` (the later chatbot lane), `list_for_product` (the page).
Plan: documentation/plans/products/PLAN-wc-seat-compat-12oct.md.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.product_compatibility import ProductCompatibility
from app.services.error_handler import (
    handle_conflict,
    handle_not_found,
    handle_validation_error,
)
from app.services.product_alternative_service import normalize_code

logger = logging.getLogger(__name__)

SOURCE_FLYER = "flyer"
SOURCE_MANUAL = "manual"

ROLE_SEAT_COVER = "seat_cover"
ROLE_WC = "wc"

#: A `-SC` segment marks a seat cover sold under a WC's code.
_SPARE_SEGMENT = "SC"


def segments(code: Optional[str]) -> list[str]:
    """The code's segments AFTER the model, normalised: `SRTWC8383-RL-MBL` -> [RL, MBL]."""
    return normalize_code(code).split("-")[1:] if code else []


def is_spare_seat_cover(code: Optional[str]) -> bool:
    return _SPARE_SEGMENT in segments(code)


# --------------------------------------------------------------------------- reads


def covers_for(db: Session, wc_product_ids: Iterable[str]) -> dict[str, list[Product]]:
    """``{wc_product_id: [seat cover Product]}``, the SKU's own links only, ordered by
    code. A SKU with no link is absent. Company-scoped like every product read."""
    return _linked(db, wc_product_ids, ProductCompatibility.wc_product_id, ProductCompatibility.seat_cover_product_id)


def wcs_for(db: Session, seat_cover_product_ids: Iterable[str]) -> dict[str, list[Product]]:
    """``{seat_cover_product_id: [WC Product]}``, ordered by code."""
    return _linked(db, seat_cover_product_ids, ProductCompatibility.seat_cover_product_id, ProductCompatibility.wc_product_id)


def _linked(db: Session, ids: Iterable[str], key_col, other_col) -> dict[str, list[Product]]:
    wanted = [str(i) for i in ids if i]
    if not wanted:
        return {}
    rows = (
        db.query(key_col, Product)
        .join(Product, Product.id == other_col)
        .filter(key_col.in_(wanted))
        .order_by(Product.product_code, Product.id)
        .all()
    )
    out: dict[str, list[Product]] = {}
    for key, other in rows:
        out.setdefault(str(key), []).append(other)
    return out


# --------------------------------------------------------------------------- product page


def _product_or_404(db: Session, product_id: str) -> Product:
    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        raise handle_not_found("Product", product_id)
    return product


def _row(link: ProductCompatibility, other: Product) -> dict:
    return {
        "id": link.id,
        "product_id": other.id,
        "product_code": other.product_code,
        "product_name": other.product_name,
        "source": link.source,
        "is_discontinued": bool(other.is_discontinued),
    }


def list_for_product(db: Session, product_id: str) -> dict:
    """Both directions: the covers that fit this product (as a WC) and the WCs this
    product fits (as a seat cover). A product is usually one or the other."""
    product = _product_or_404(db, product_id)
    covers = (
        db.query(ProductCompatibility, Product)
        .join(Product, Product.id == ProductCompatibility.seat_cover_product_id)
        .filter(ProductCompatibility.wc_product_id == product.id)
        .order_by(Product.product_code, Product.id)
        .all()
    )
    fits = (
        db.query(ProductCompatibility, Product)
        .join(Product, Product.id == ProductCompatibility.wc_product_id)
        .filter(ProductCompatibility.seat_cover_product_id == product.id)
        .order_by(Product.product_code, Product.id)
        .all()
    )
    return {
        "covers": [_row(link, other) for link, other in covers],
        "fits": [_row(link, other) for link, other in fits],
    }


def add_manual(
    db: Session, product_id: str, other_product_id: str, other_role: str, user_id: Optional[str]
) -> dict:
    """Link this product to another, SKU to SKU. `other_role` names what the OTHER product
    is: `seat_cover` (this page is the WC) or `wc` (this page is the seat cover)."""
    if other_role not in (ROLE_SEAT_COVER, ROLE_WC):
        raise handle_validation_error("Pick whether the other product is a seat cover or a WC.")
    product = _product_or_404(db, product_id)
    if str(other_product_id) == str(product.id):
        raise handle_validation_error("A product cannot fit itself.")
    other = (
        db.query(Product)
        .filter(Product.id == other_product_id, Product.company_id == product.company_id)
        .first()
    )
    if other is None:
        raise handle_not_found("Product", other_product_id)
    cover, wc = (other, product) if other_role == ROLE_SEAT_COVER else (product, other)
    if is_spare_seat_cover(wc.product_code):
        raise handle_validation_error(f"{wc.product_code} is a seat cover, not a WC.")
    # Either orientation: the same two products linked the other way round would say the
    # WC is a seat cover that fits the seat cover.
    exists = (
        db.query(ProductCompatibility.id)
        .filter(
            or_(
                (ProductCompatibility.seat_cover_product_id == cover.id)
                & (ProductCompatibility.wc_product_id == wc.id),
                (ProductCompatibility.seat_cover_product_id == wc.id)
                & (ProductCompatibility.wc_product_id == cover.id),
            )
        )
        .first()
    )
    if exists:
        raise handle_conflict(f"{cover.product_code} is already linked to {wc.product_code}.")
    link = ProductCompatibility(
        company_id=product.company_id,
        seat_cover_product_id=cover.id,
        wc_product_id=wc.id,
        source=SOURCE_MANUAL,
        created_by=user_id,
    )
    db.add(link)
    try:
        db.commit()
    except IntegrityError:
        # Two adds of the same pair racing: the unique pair index is the arbiter.
        db.rollback()
        raise handle_conflict(f"{cover.product_code} is already linked to {wc.product_code}.")
    db.refresh(link)
    return _row(link, other)


def get_link(db: Session, link_id: str) -> Optional[ProductCompatibility]:
    return db.query(ProductCompatibility).filter(ProductCompatibility.id == link_id).first()


def unlink(db: Session, link_id: str) -> None:
    """Hard delete one link. Already gone ends quietly: the outcome asked for holds. A link
    that never existed is refused at park time (`pending_actions`)."""
    link = get_link(db, link_id)
    if link is None:
        return
    db.delete(link)
    db.commit()
