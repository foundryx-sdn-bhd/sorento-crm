"""Seat cover <-> WC compatibility: the links, the flyer review and the one lookup.

A link (`product_compatibility`) says "seat cover X fits WC SKU Y", same company, one SKU
to one SKU. Owner ruling 12 Oct: match only what the flyer prints, fabricate nothing. So
there is no model-wide link and no inheritance: a WC SKU has a cover only when a link names
it, and a WC SKU with no link has no answer.

Two writers:

* the flyer review (source `flyer`): `flyer_review` maps every printed WC code
  (`seat_cover_flyer_data`) onto the catalogue's SKUs and the owner ticks SKUs;
  `apply_flyer` re-derives each ticked SKU and writes its links. A SKU whose mapping is not
  unambiguous is UNRESOLVED and is never written.
* a person on the product page (source `manual`).

A `-SC` SKU (`SRTWC8383-SC`) is a seat cover, never a WC (owner ruling 12 Oct, Q2).

Readers: `covers_for` / `wcs_for` (the later chatbot lane), `list_for_product` (the page).
Plan: documentation/plans/products/PLAN-wc-seat-compat-12oct.md.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Iterable, Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base import company_scope
from app.models.product import Product
from app.models.product_compatibility import ProductCompatibility
from app.services.error_handler import (
    AppException,
    handle_conflict,
    handle_not_found,
    handle_validation_error,
)
from app.services.product_alternative_service import normalize_code
from app.services.product_family import family_code_for
from app.services.product_spec_derivation import FINISH_SUFFIXES
from app.services.seat_cover_flyer_data import FLYER_ROW_FINISH, FLYER_ROWS

logger = logging.getLogger(__name__)

SOURCE_FLYER = "flyer"
SOURCE_MANUAL = "manual"

ROLE_SEAT_COVER = "seat_cover"
ROLE_WC = "wc"

#: A `-SC` segment marks a seat cover sold under a WC's code.
_SPARE_SEGMENT = "SC"
#: Finish code segments: the spec engine's own list (one vocabulary), less SC, which on a
#: WC code means the seat cover above, plus the finishes WC codes carry that the spec engine
#: does not know (matt grey / black / white, black-white, pink, blue: measured on the
#: catalogue's printed-model SKUs, 12 Oct). Only used to NAME why a SKU is unresolved: a
#: segment outside `NEUTRAL_SEGMENTS` is unresolved whether or not it is listed here.
FINISH_SEGMENTS = (frozenset(FINISH_SUFFIXES) - {_SPARE_SEGMENT}) | {
    "MG", "MK", "MW", "BW", "PINK", "BLUE",
}
#: The variant markers a SKU may carry beyond its printed code and still be that printed
#: WC: trap / outlet (RL, SH, P, S, and S150-style), the UF seat, packaging markers (NEW,
#: N, PP, the product-alternatives rule's own) and a bare size number. Anything else (a
#: finish, a part such as LID, an unexplained segment) leaves the SKU unresolved: a guess
#: about what it means would be a fabricated link (owner ruling 12 Oct).
NEUTRAL_SEGMENTS = frozenset({"RL", "SH", "P", "S", "UF", "NEW", "N", "PP"})
_NEUTRAL_RE = re.compile(r"^(S)?\d+$")

SEAT_COVER_SPEC_KEY = "seat_cover"


def _segments(code: Optional[str]) -> list[str]:
    """The code's segments AFTER the model, normalised: `SRTWC8383-RL-MBL` -> [RL, MBL]."""
    return normalize_code(code).split("-")[1:] if code else []


def is_spare_seat_cover(code: Optional[str]) -> bool:
    return _SPARE_SEGMENT in _segments(code)


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


# --------------------------------------------------------------------------- flyer review


def _printed_by_family() -> dict[str, list[dict]]:
    """Every printed WC code, grouped by its model, in flyer order."""
    out: dict[str, list[dict]] = defaultdict(list)
    for row, covers, wcs in FLYER_ROWS:
        for wc in wcs:
            out[family_code_for(wc)].append(
                {
                    "flyer_row": row,
                    "wc_code": wc,
                    "segments": frozenset(_segments(wc)),
                    "note_finish": FLYER_ROW_FINISH.get(row),
                    "cover_codes": list(covers),
                }
            )
    return out


def _classify(sku: Product, printed: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    """The one printed code this SKU belongs to, or why it belongs to none.

    The most specific printed code whose segments the SKU holds whole. Unresolved when no
    printed code fits, when two fit equally, when the SKU carries a segment the flyer
    prints only on other codes of the model (a plain `SRTWC8066-S` when only
    `SRTWC8066-S-<colour>` is printed), a finish the flyer does not print for it, or any
    other segment that is not a plain variant marker (`NEUTRAL_SEGMENTS`)."""
    if family_code_for(sku.product_code) != sku.family_code:
        # Grouped under the model by hand (`family_code_manual`): its own code is not the
        # printed model, so nothing on the flyer names it.
        return None, "not_printed"
    segs = set(_segments(sku.product_code))
    fits = [p for p in printed if p["segments"] <= segs]
    if not fits:
        return None, "not_printed"
    best_size = max(len(p["segments"]) for p in fits)
    best = [p for p in fits if len(p["segments"]) == best_size]
    if len(best) > 1:
        return None, "ambiguous"
    match = best[0]
    extra = segs - match["segments"]
    if match["note_finish"]:
        extra.discard(match["note_finish"])
    printed_elsewhere = set().union(*(p["segments"] for p in printed))
    if extra & printed_elsewhere:
        return None, "sub_model_not_printed"
    if extra & FINISH_SEGMENTS:
        return None, "finish_not_printed"
    if any(seg not in NEUTRAL_SEGMENTS and not _NEUTRAL_RE.match(seg) for seg in extra):
        return None, "unknown_segment"
    return match, None


def _company_names(db: Session, company_ids: set) -> dict[str, str]:
    from app.models.company import Company

    if not company_ids:
        return {}
    return {
        str(c.id): c.name
        for c in db.query(Company).filter(Company.id.in_(list(company_ids))).all()
    }


def _covers_by_company(db: Session) -> dict[tuple[str, str], Product]:
    """`(company_id, normalised code) -> cover`, for every printed cover code in scope."""
    codes = sorted({normalize_code(c) for _, covers, _ in FLYER_ROWS for c in covers})
    from app.services.product_alternative_service import _norm_sql

    rows = db.query(Product).filter(_norm_sql(Product.product_code).in_(codes)).all()
    return {(str(p.company_id), normalize_code(p.product_code)): p for p in rows}


def _resolve_sku(sku: Product, printed: list[dict], covers: dict) -> dict:
    match, reason = _classify(sku, printed)
    if match is None:
        return {"status": "unresolved", "reason": reason, "match": None, "covers": []}
    found = [
        covers[(str(sku.company_id), normalize_code(code))]
        for code in match["cover_codes"]
        if (str(sku.company_id), normalize_code(code)) in covers
    ]
    if not found:
        return {"status": "cover_missing", "reason": None, "match": match, "covers": []}
    return {"status": "ready", "reason": None, "match": match, "covers": found}


def flyer_review(db: Session) -> dict:
    """What the flyer says, laid over the catalogue in the caller's scope: one entry per
    printed model, its printed codes with their covers, and every SKU of the model with
    the printed code it maps to, or why it maps to none. Writes nothing."""
    printed_by_family = _printed_by_family()
    covers = _covers_by_company(db)
    found_codes = {code for (_, code) in covers}

    skus = (
        db.query(Product)
        .filter(Product.family_code.in_(sorted(printed_by_family)))
        .order_by(Product.product_code, Product.company_id)
        .all()
    )
    skus = [s for s in skus if not is_spare_seat_cover(s.product_code)]
    linked = defaultdict(set)
    if skus:
        for wc_id, cover_id in db.query(
            ProductCompatibility.wc_product_id, ProductCompatibility.seat_cover_product_id
        ).filter(ProductCompatibility.wc_product_id.in_([s.id for s in skus])):
            linked[str(wc_id)].add(str(cover_id))
    names = _company_names(db, {str(s.company_id) for s in skus})
    multi_company = len(names) > 1

    by_family: dict[str, list[Product]] = defaultdict(list)
    for sku in skus:
        by_family[sku.family_code].append(sku)

    counts = defaultdict(int)
    models = []
    for family, printed in printed_by_family.items():
        rows = []
        for sku in by_family.get(family, []):
            resolved = _resolve_sku(sku, printed, covers)
            status = resolved["status"]
            if status == "ready" and {c.id for c in resolved["covers"]} <= linked[sku.id]:
                status = "linked"
            counts[status] += 1
            match = resolved["match"]
            rows.append(
                {
                    "product_id": sku.id,
                    "product_code": sku.product_code,
                    "product_name": sku.product_name,
                    "company_name": names.get(str(sku.company_id)) if multi_company else None,
                    "is_active": bool(sku.is_active),
                    "status": status,
                    "reason": resolved["reason"],
                    "printed_code": match["wc_code"] if match else None,
                    "flyer_row": match["flyer_row"] if match else None,
                    "cover_codes": [c.product_code for c in resolved["covers"]],
                    "extra_segments": sorted(
                        set(_segments(sku.product_code)) - (match["segments"] if match else set())
                    ),
                }
            )
        models.append(
            {
                "family_code": family,
                "printed": [
                    {
                        "flyer_row": p["flyer_row"],
                        "wc_code": p["wc_code"],
                        "covers": [
                            {"code": code, "found": normalize_code(code) in found_codes}
                            for code in p["cover_codes"]
                        ],
                    }
                    for p in printed
                ],
                "skus": rows,
            }
        )

    all_cover_codes = {normalize_code(c) for _, cs, _ in FLYER_ROWS for c in cs}
    return {
        "models": models,
        "counts": {
            "models": len(models),
            "models_missing": sum(1 for m in models if not m["skus"]),
            "covers_missing": len(all_cover_codes - found_codes),
            "ready": counts["ready"],
            "linked": counts["linked"],
            "unresolved": counts["unresolved"],
            "cover_missing": counts["cover_missing"],
        },
        "seat_cover_spec": seat_cover_spec_state(db),
    }


def apply_flyer(db: Session, wc_product_ids: list[str], user_id: Optional[str]) -> dict:
    """Write the links for the ticked SKUs, source `flyer`. Each SKU is re-derived here
    from the flyer and the live catalogue, never trusted from the screen: an id that is
    not a printed model's SKU, or that no longer resolves, is skipped with its reason.
    Idempotent: a link that exists is counted, not written again."""
    printed_by_family = _printed_by_family()
    covers = _covers_by_company(db)
    ids = list(dict.fromkeys(str(i) for i in wc_product_ids))
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_(ids)).all()} if ids else {}

    linked = written = 0
    skipped = []
    for pid in ids:
        sku = products.get(pid)
        printed = printed_by_family.get(sku.family_code) if sku is not None else None
        if sku is None or not printed or is_spare_seat_cover(sku.product_code):
            skipped.append({"product_id": pid, "reason": "not_on_flyer"})
            continue
        resolved = _resolve_sku(sku, printed, covers)
        if resolved["status"] != "ready":
            skipped.append({"product_id": pid, "reason": resolved["status"]})
            continue
        existing = {
            str(c)
            for (c,) in db.query(ProductCompatibility.seat_cover_product_id).filter(
                ProductCompatibility.wc_product_id == sku.id
            )
        }
        new = [c for c in resolved["covers"] if c.id not in existing]
        if not new:
            linked += 1
            continue
        for cover in new:
            db.add(
                ProductCompatibility(
                    company_id=sku.company_id,
                    seat_cover_product_id=cover.id,
                    wc_product_id=sku.id,
                    source=SOURCE_FLYER,
                    created_by=user_id,
                )
            )
            written += 1
    try:
        db.commit()
    except IntegrityError:
        # Two applies of the same SKUs racing: the unique pair index is the arbiter.
        db.rollback()
        raise handle_conflict("These SKUs were linked at the same moment. Reload and try again.")
    return {"linked": written, "already_linked": linked, "skipped": skipped}


# --------------------------------------------------------------------------- old spec


def _seat_cover_registry(db: Session):
    from app.models.product_spec import ProductSpecRegistry

    return (
        db.query(ProductSpecRegistry)
        .filter(ProductSpecRegistry.spec_key == SEAT_COVER_SPEC_KEY)
        .one_or_none()
    )


def _spec_rows_holding(db: Session):
    """Every product holding a `seat_cover` value, in EVERY company: the registry key is
    one for the whole install, so retiring it is too (`apply_spec_values` takes the same
    all-companies scope to write each code's copies)."""
    from app.models.product_spec import ProductSpecifications

    with company_scope(db, None):
        return (
            db.query(ProductSpecifications, Product)
            .join(Product, Product.id == ProductSpecifications.product_id)
            .filter(ProductSpecifications.values.has_key(SEAT_COVER_SPEC_KEY))  # noqa: W601
            .order_by(Product.product_code)
            .all()
        )


def _flyer_applied_everywhere(db: Session) -> bool:
    """The old spec is replaced: flyer links exist, in EVERY company that holds a value.

    The retire reaches every company (the key is install-wide), so its gate looks at every
    company too: one company applying the flyer must not take another company's values
    away before that company has links to replace them (security review, PR #1590)."""
    with company_scope(db, None):
        linked = {
            str(company_id)
            for (company_id,) in db.query(ProductCompatibility.company_id)
            .filter(ProductCompatibility.source == SOURCE_FLYER)
            .distinct()
        }
        holding = {str(product.company_id) for _, product in _spec_rows_holding(db)}
    return bool(linked) and holding <= linked


def seat_cover_spec_state(db: Session) -> Optional[dict]:
    """The old hand-typed `Seat Cover` spec the links replace. None when the key does not
    exist (a fresh install)."""
    registry = _seat_cover_registry(db)
    if registry is None:
        return None
    value_count = len(_spec_rows_holding(db))
    retirable = bool(registry.is_active) or value_count > 0
    return {
        "registry_id": registry.id,
        "is_active": bool(registry.is_active),
        "value_count": value_count,
        "can_retire": retirable and _flyer_applied_everywhere(db),
    }


def assert_can_retire(db: Session, registry_id: str) -> None:
    """Park-time check for `seat_cover_spec.retire`: the key exists, and the flyer links it
    is replaced by have been applied (owner ruling 12 Oct, Q4)."""
    registry = _seat_cover_registry(db)
    if registry is None or str(registry.id) != str(registry_id):
        raise handle_not_found("Seat Cover specification", registry_id)
    if not _flyer_applied_everywhere(db):
        raise AppException(
            status_code=409,
            message=(
                "Apply the seat cover flyer first, in every company holding the old spec; "
                "it stays until it is replaced."
            ),
            code="seat_cover_flyer_not_applied",
        )


def retire_seat_cover_spec(db: Session, registry_id: str, user_id: Optional[str] = None) -> dict:
    """Remove every `seat_cover` value, set the key inactive and clear its derivation rules,
    with one audit row holding what was removed.

    Inactive alone would not stop the re-derive each removal triggers: derivation reads
    every key's rules, active or not (`product_spec_derivation.configured_rules`). An empty
    rule list means "this key has no rules", so the values cannot come back.

    The gate is checked again here, at commit time: links unlinked during the countdown
    leave the old spec in place rather than retire it with nothing replacing it."""
    from app.services.audit_service import log_audit
    from app.services.product_spec_write import apply_spec_values

    registry = _seat_cover_registry(db)
    if registry is None or str(registry.id) != str(registry_id):
        # Already gone: nothing left to retire.
        return {"removed": 0}
    if not _flyer_applied_everywhere(db):
        return {"removed": 0}
    holding = _spec_rows_holding(db)
    removed = [
        {
            "product_code": product.product_code,
            "company_id": str(product.company_id),
            "value": (spec.values or {}).get(SEAT_COVER_SPEC_KEY, {}).get("value"),
        }
        for spec, product in holding
    ]
    was_active = bool(registry.is_active)
    old_rules = registry.derivation_rules
    registry.is_active = False
    registry.derivation_rules = []
    db.flush()
    for code in sorted({row["product_code"] for row in removed}):
        apply_spec_values(
            db,
            code,
            [{"spec_key": SEAT_COVER_SPEC_KEY, "op": "revert"}],
            actor={"id": user_id} if user_id else None,
            commit=False,
        )
    log_audit(
        db,
        "product_spec_registry",
        str(registry.id),
        "UPDATE",
        old_values={"is_active": was_active, "derivation_rules": old_rules, "values": removed},
        new_values={"is_active": False, "derivation_rules": [], "values": []},
        user_id=user_id,
        description="Seat Cover spec retired: replaced by seat cover compatibility links.",
        event="seat_cover_spec.retire",
    )
    db.commit()
    return {"removed": len(removed)}
