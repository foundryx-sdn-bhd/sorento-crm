"""Accessory <-> host compatibility: the links and the lookups.

A link (`product_compatibility`) says "accessory X fits host SKU Y", same company, one SKU
to one SKU, of one `kind`: a seat cover on a WC, a mirror on a cabinet, a shelf on a cabinet.
The accessory is `seat_cover_product_id`, the host `wc_product_id` (columns not renamed).
Owner ruling 12 Oct: match only what the flyer prints, fabricate nothing. So there is no
model-wide link and no inheritance: a host SKU has an accessory only when a link names it.

Two writers:

* the one-off flyer scripts (source `flyer`): `scripts/oneoff/link_seat_cover_flyer.py` and
  `link_mirror_shelf_flyer.py`;
* a person on the product page's Compatible section, Variants tab (source `manual`), which
  is where the links are kept from then on.

A `-SC` SKU (`SRTWC8383-SC`) is a seat cover, never a WC (owner ruling 12 Oct, Q2).

Readers: `covers_for` / `wcs_for` (seat cover links only, the chatbot's fits filter),
`accessories_for` / `hosts_for` (any kind), `list_for_product` (the page).
Plans: documentation/plans/products/PLAN-wc-seat-compat-12oct.md,
documentation/plans/products/PLAN-mirror-shelf-links-14oct.md.
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

KIND_SEAT_COVER = "seat_cover"
KIND_MIRROR = "mirror"
KIND_SHELF = "shelf"
#: kind -> (accessory noun, host noun); the dict order is the display order.
KINDS = {
    KIND_SEAT_COVER: ("seat cover", "WC"),
    KIND_MIRROR: ("mirror", "cabinet"),
    KIND_SHELF: ("shelf", "cabinet"),
}

ROLE_ACCESSORY = "accessory"
ROLE_HOST = "host"
#: Legacy roles, seat cover links only.
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
    """``{wc_product_id: [seat cover Product]}``, the SKU's own seat cover links only,
    ordered by code. A SKU with no link is absent. Company-scoped like every product read."""
    return accessories_for(db, wc_product_ids, KIND_SEAT_COVER)


def wcs_for(db: Session, seat_cover_product_ids: Iterable[str]) -> dict[str, list[Product]]:
    """``{seat_cover_product_id: [WC Product]}``, seat cover links only, ordered by code."""
    return hosts_for(db, seat_cover_product_ids, KIND_SEAT_COVER)


def accessories_for(db: Session, host_ids: Iterable[str], kind: str) -> dict[str, list[Product]]:
    """``{host_id: [accessory Product]}`` for one kind, ordered by code."""
    return _linked(
        db, host_ids, kind, ProductCompatibility.wc_product_id, ProductCompatibility.seat_cover_product_id
    )


def hosts_for(db: Session, accessory_ids: Iterable[str], kind: str) -> dict[str, list[Product]]:
    """``{accessory_id: [host Product]}`` for one kind, ordered by code."""
    return _linked(
        db, accessory_ids, kind, ProductCompatibility.seat_cover_product_id, ProductCompatibility.wc_product_id
    )


def _linked(db: Session, ids: Iterable[str], kind: str, key_col, other_col) -> dict[str, list[Product]]:
    wanted = [str(i) for i in ids if i]
    if not wanted:
        return {}
    rows = (
        db.query(key_col, Product)
        .join(Product, Product.id == other_col)
        .filter(key_col.in_(wanted), ProductCompatibility.kind == kind)
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
        "kind": link.kind,
    }


def list_for_product(db: Session, product_id: str) -> dict:
    """Both directions, every kind: `covers` are the accessories that fit this product (as
    the host), `fits` the hosts this product fits (as an accessory). Ordered by kind, then
    code."""
    product = _product_or_404(db, product_id)
    kind_order = list(KINDS)

    def rows(host_side: bool) -> list[dict]:
        mine, theirs = (
            (ProductCompatibility.wc_product_id, ProductCompatibility.seat_cover_product_id)
            if host_side
            else (ProductCompatibility.seat_cover_product_id, ProductCompatibility.wc_product_id)
        )
        found = (
            db.query(ProductCompatibility, Product)
            .join(Product, Product.id == theirs)
            .filter(mine == product.id)
            .order_by(Product.product_code, Product.id)
            .all()
        )
        found.sort(key=lambda pair: kind_order.index(pair[0].kind))
        return [_row(link, other) for link, other in found]

    return {"covers": rows(True), "fits": rows(False)}


def add_manual(
    db: Session,
    product_id: str,
    other_product_id: str,
    other_role: str,
    user_id: Optional[str],
    kind: str = KIND_SEAT_COVER,
) -> dict:
    """Link this product to another, SKU to SKU. `other_role` names what the OTHER product
    is: `accessory` or `host`. The legacy `seat_cover` (== accessory) and `wc` (== host)
    only go with kind `seat_cover`."""
    if kind not in KINDS:
        raise handle_validation_error("Pick a link type: seat cover, mirror or shelf.")
    if other_role in (ROLE_SEAT_COVER, ROLE_WC) and kind != KIND_SEAT_COVER:
        raise handle_validation_error(f"The role {other_role} only fits seat cover links.")
    if other_role not in (ROLE_ACCESSORY, ROLE_HOST, ROLE_SEAT_COVER, ROLE_WC):
        accessory_noun, host_noun = KINDS[kind]
        raise handle_validation_error(
            f"Pick whether the other product is a {accessory_noun} or a {host_noun}."
        )
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
    other_is_accessory = other_role in (ROLE_ACCESSORY, ROLE_SEAT_COVER)
    accessory, host = (other, product) if other_is_accessory else (product, other)
    if kind == KIND_SEAT_COVER and is_spare_seat_cover(host.product_code):
        raise handle_validation_error(f"{host.product_code} is a seat cover, not a WC.")
    # Either orientation: the same two products linked the other way round would say the
    # host is an accessory that fits the accessory.
    exists = (
        db.query(ProductCompatibility.id)
        .filter(
            ProductCompatibility.kind == kind,
            or_(
                (ProductCompatibility.seat_cover_product_id == accessory.id)
                & (ProductCompatibility.wc_product_id == host.id),
                (ProductCompatibility.seat_cover_product_id == host.id)
                & (ProductCompatibility.wc_product_id == accessory.id),
            ),
        )
        .first()
    )
    if exists:
        raise handle_conflict(f"{accessory.product_code} is already linked to {host.product_code}.")
    link = ProductCompatibility(
        company_id=product.company_id,
        seat_cover_product_id=accessory.id,
        wc_product_id=host.id,
        kind=kind,
        source=SOURCE_MANUAL,
        created_by=user_id,
    )
    db.add(link)
    try:
        db.commit()
    except IntegrityError:
        # Two adds of the same pair racing: the unique pair index is the arbiter.
        db.rollback()
        raise handle_conflict(f"{accessory.product_code} is already linked to {host.product_code}.")
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
