#!/usr/bin/env python3
"""One-off: link WC SKUs to the seat covers the seat cover flyer prints for them.

WHY
---
The flyer "SORENTO SEAT COVER COMPATIBLE WATER CLOSET MODEL" (END USER, UPDATE 01.04.2026,
footer 2025-08) is the only source of which seat cover fits which WC. Owner decision 12 Oct:
link it once with this script; from then on people keep the links by hand in the product
page's Compatible section (Variants tab). There is no review screen.

WHAT IT DOES
------------
`FLYER_ROWS` below is the flyer's page 1, transcribed by code exactly as printed (35 rows, 49
pairs). Page 2 lists seven intelligent-seat WCs with no seat cover code; they are not here,
because there is nothing to link (never fabricate).

For every SKU of a printed model (`products.family_code`), in every company:

* LINK when a printed row names it unambiguously: the most specific printed code of its model
  whose segments the SKU holds as whole segments (`SRTWC8383-RL-MBL` -> printed
  `SRTWC8383-MBL` -> `SRTSCBD823-MBL`, never BL or the plain row), and every other segment of
  the SKU is a plain variant marker (`NEUTRAL_SEGMENTS`, a size number, `S150`). Row 6 links
  both its covers.
* UNRESOLVED (printed, never linked) otherwise, with the reason:
  `ambiguous` (two printed codes fit equally), `sub_model_not_printed` (a segment the flyer
  prints only on other codes of the model, e.g. a plain `SRTWC8066-S`), `finish_not_printed`,
  `unknown_segment`, `not_printed` (grouped under the model by hand).
* MISSING: a printed model with no SKU, or a SKU whose printed seat cover is not in that
  company's catalogue.
* `-SC` SKUs are seat covers, never WCs: never linked as one and never listed.

A link is written with source `flyer` in the WC's own company. A pair already linked (by this
script or by a person) is counted, never duplicated, so a second run writes nothing.

`--retire-spec` (only together with `--apply`) then removes every hand-typed `seat_cover`
spec value, deactivates the key and clears its rules, with one audit row holding what was
removed. It refuses while any company holding a value has no flyer link to replace it.

DRY-RUN unless --apply: it prints what it would link and writes nothing.

    venv/bin/python scripts/oneoff/link_seat_cover_flyer.py
    venv/bin/python scripts/oneoff/link_seat_cover_flyer.py --apply
    venv/bin/python scripts/oneoff/link_seat_cover_flyer.py --apply --retire-spec

Plan: documentation/plans/products/PLAN-wc-seat-compat-12oct.md.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from typing import Optional, Sequence, TextIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.models.base import company_scope  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.product_compatibility import ProductCompatibility  # noqa: E402
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry  # noqa: E402
from app.services.product_alternative_service import _norm_sql, normalize_code  # noqa: E402
from app.services.product_compatibility_service import (  # noqa: E402
    SOURCE_FLYER,
    is_spare_seat_cover,
    segments,
)
from app.services.product_family import family_code_for  # noqa: E402
from app.services.product_spec_derivation import FINISH_SUFFIXES  # noqa: E402

#: (flyer row, seat cover codes as printed, WC model codes as printed). Row 6 prints two
#: covers for one WC; every other row prints one.
FLYER_ROWS: tuple[tuple[int, tuple[str, ...], tuple[str, ...]], ...] = (
    (1, ("SRTSCBD319-UF",), ("SRTWC8320", "SRTWC8319", "SRTWC200")),
    (2, ("SRTSCBD330-UF",), ("SRTWC8088",)),
    (3, ("SRTSCBD331-UF",), ("SRTWC8036", "SRTWC8354", "SRTWC8035", "SRTWC8383", "SRTWC8517")),
    (4, ("SRTSCBD332-UF",), ("SRTWC8357", "SRTWC8366")),
    (5, ("SRTSCBD289-UF",), ("SRTWC289", "SRTWC6031")),
    (6, ("SRTSCBD333-UF", "SRTSCBD333A-UF"), ("SRTWC8122",)),
    (7, ("SRTSCBD335-UF",), ("SRTWC6015",)),
    (8, ("SRTSCBD336-UF",), ("SRTWC6005",)),
    (9, ("SRTSCBD337-UF",), ("SRTWC6020", "SRTWC6021")),
    (10, ("SRTSCBD338-UF",), ("SRTWC6022",)),
    (11, ("SRTSCBD403",), ("SRTWC8504",)),
    (12, ("SRTSCBD324",), ("SRTWC8317",)),
    (13, ("SRTSCBD323",), ("SRTWC8318",)),
    (14, ("SRTSCBD320",), ("SRTWC8152", "SRTWC8356")),
    (15, ("SRTSCBD407",), ("SRTWC286", "SRTWC8355", "SRTWC8066")),
    (16, ("SRTSCBD401",), ("SRTWC7614",)),
    (17, ("SRTSCBD315",), ("SRTWC287",)),
    (18, ("SRTSCBD313",), ("SRTWC287A",)),
    (19, ("SRTSCBD339-UF",), ("SRTWC6030",)),
    (20, ("SRTSCBD408",), ("SRTWC8518",)),
    (21, ("SRTSCBD325",), ("SRTWC201",)),
    (22, ("SRTSCBD326",), ("SRTWC202",)),
    (23, ("SRTSCBD327",), ("SRTWC203",)),
    (24, ("SRTSCBD823-MBL",), ("SRTWC8383-MBL",)),
    (25, ("SRTSCBD824-BL",), ("SRTWC8383-BL", "SRTWC8152-BL")),
    (26, ("SRTSCBD834-MBL",), ("SRTWC8318-MBL",)),
    (27, ("SRTSCBD833-BL",), ("SRTWC8318-BL",)),
    (28, ("SRTSCBD825-GY",), ("SRTWC8383-GY",)),
    (29, ("SRTSCBD826-RG",), ("SRTWC8383-RG",)),
    (30, ("SRTSCBD836-RG",), ("SRTWC8318-RG",)),
    (31, ("SRTSCBD835-GY",), ("SRTWC8318-GY",)),
    (32, ("SRTSCBD841-BL",), ("SRTWC8066-S-BL",)),
    (33, ("SRTSCBD842-MBL",), ("SRTWC8066-S-MBL",)),
    (34, ("SRTSCBD843-GY",), ("SRTWC8066-S-GY",)),
    (35, ("SRTSCBD844-RG",), ("SRTWC8066-S-RG",)),
)

#: A finish the flyer prints as a note beside the WC code rather than in it: row 22 prints
#: SRTWC202 with "Pink" and a pink dot, row 23 SRTWC203 with "Blue" and a blue dot.
FLYER_ROW_FINISH: dict[int, str] = {22: "PINK", 23: "BLUE"}

#: Finish code segments, only used to NAME why a SKU is unresolved: the spec engine's list
#: (less SC, a seat cover on a WC code) plus the finishes WC codes carry that it does not know
#: (measured on the catalogue's printed-model SKUs, 12 Oct).
FINISH_SEGMENTS = (frozenset(FINISH_SUFFIXES) - {"SC"}) | {"MG", "MK", "MW", "BW", "PINK", "BLUE"}
#: The variant markers a SKU may carry beyond its printed code and still be that printed WC:
#: trap / outlet, the UF seat, packaging markers, a bare size number. Anything else leaves the
#: SKU unresolved: a guess about what it means would be a fabricated link.
NEUTRAL_SEGMENTS = frozenset({"RL", "SH", "P", "S", "UF", "NEW", "N", "PP"})
_NEUTRAL_RE = re.compile(r"^(S)?\d+$")

SEAT_COVER_SPEC_KEY = "seat_cover"


def flyer_pairs() -> list[tuple[int, str, str]]:
    """Every (row, WC code, seat cover code) the flyer prints, in flyer order: 49 pairs."""
    return [(row, wc, cover) for row, covers, wcs in FLYER_ROWS for wc in wcs for cover in covers]


def _printed_by_family() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for row, covers, wcs in FLYER_ROWS:
        for wc in wcs:
            out[family_code_for(wc)].append(
                {
                    "flyer_row": row,
                    "wc_code": wc,
                    "segments": frozenset(segments(wc)),
                    "note_finish": FLYER_ROW_FINISH.get(row),
                    "cover_codes": list(covers),
                }
            )
    return out


def classify(sku: Product, printed: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    """The one printed code this SKU belongs to, or why it belongs to none."""
    if family_code_for(sku.product_code) != sku.family_code:
        # Grouped under the model by hand (`family_code_manual`): nothing printed names it.
        return None, "not_printed"
    segs = set(segments(sku.product_code))
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
    if extra & set().union(*(p["segments"] for p in printed)):
        return None, "sub_model_not_printed"
    if extra & FINISH_SEGMENTS:
        return None, "finish_not_printed"
    if any(seg not in NEUTRAL_SEGMENTS and not _NEUTRAL_RE.match(seg) for seg in extra):
        return None, "unknown_segment"
    return match, None


def plan(db) -> dict:
    """What the flyer says, laid over the whole catalogue (every company). Writes nothing."""
    printed_by_family = _printed_by_family()
    cover_codes = sorted({normalize_code(c) for _, cs, _ in FLYER_ROWS for c in cs})
    links, unresolved, missing = [], [], []
    with company_scope(db, None):
        covers = {
            (str(p.company_id), normalize_code(p.product_code)): p
            for p in db.query(Product).filter(_norm_sql(Product.product_code).in_(cover_codes))
        }
        skus = (
            db.query(Product)
            .filter(Product.family_code.in_(sorted(printed_by_family)))
            .order_by(Product.product_code, Product.company_id)
            .all()
        )
        linked = {
            (str(c), str(w))
            for c, w in db.query(
                ProductCompatibility.seat_cover_product_id, ProductCompatibility.wc_product_id
            )
        }
    skus = [s for s in skus if not is_spare_seat_cover(s.product_code)]
    by_family = defaultdict(list)
    for sku in skus:
        by_family[sku.family_code].append(sku)

    for family, printed in printed_by_family.items():
        if not by_family.get(family):
            for p in printed:
                for cover in p["cover_codes"]:
                    missing.append(
                        {
                            "flyer_row": p["flyer_row"],
                            "printed_code": p["wc_code"],
                            "cover_code": cover,
                            "wc_code": None,
                            "reason": "no_sku",
                        }
                    )
            continue
        for sku in by_family[family]:
            match, reason = classify(sku, printed)
            if match is None:
                unresolved.append({"family_code": family, "wc_code": sku.product_code, "reason": reason})
                continue
            for code in match["cover_codes"]:
                cover = covers.get((str(sku.company_id), normalize_code(code)))
                base = {
                    "flyer_row": match["flyer_row"],
                    "printed_code": match["wc_code"],
                    "cover_code": code,
                    "wc_code": sku.product_code,
                }
                if cover is None:
                    missing.append({**base, "reason": "cover_missing"})
                    continue
                links.append(
                    {
                        **base,
                        "company_id": str(sku.company_id),
                        "wc_id": sku.id,
                        "cover_id": cover.id,
                        # Either orientation counts: a pair already linked the other way
                        # round must not gain a second, opposite row.
                        "already": (str(cover.id), str(sku.id)) in linked
                        or (str(sku.id), str(cover.id)) in linked,
                        "inactive": not sku.is_active,
                    }
                )
    return {"links": links, "unresolved": unresolved, "missing": missing}


def apply(db, the_plan: dict) -> tuple[int, int]:
    """Write the plan's links (source `flyer`). Returns (written, already linked)."""
    written = already = 0
    with company_scope(db, None):
        for link in the_plan["links"]:
            if link["already"]:
                already += 1
                continue
            db.add(
                ProductCompatibility(
                    company_id=link["company_id"],
                    seat_cover_product_id=link["cover_id"],
                    wc_product_id=link["wc_id"],
                    source=SOURCE_FLYER,
                )
            )
            written += 1
        db.flush()
    return written, already


def _spec_rows_holding(db) -> list:
    with company_scope(db, None):
        return (
            db.query(ProductSpecifications, Product)
            .join(Product, Product.id == ProductSpecifications.product_id)
            .filter(ProductSpecifications.values.has_key(SEAT_COVER_SPEC_KEY))  # noqa: W601
            .order_by(Product.product_code)
            .all()
        )


def companies_without_flyer_links(db) -> list[str]:
    """Companies that hold a `seat_cover` value but no flyer link to replace it."""
    holding = _spec_rows_holding(db)
    with company_scope(db, None):
        linked = {
            str(c)
            for (c,) in db.query(ProductCompatibility.company_id)
            .filter(ProductCompatibility.source == SOURCE_FLYER)
            .distinct()
        }
        names = {str(c.id): c.name for c in db.query(Company).all()}
    blocked = sorted({str(p.company_id) for _, p in holding} - linked)
    return [names.get(cid, cid) for cid in blocked]


def retire_spec(db) -> int:
    """Remove every `seat_cover` value, deactivate the key and clear its rules (derivation
    reads every key's rules, active or not, so inactive alone would let a re-derive put the
    values back). One audit row records what was removed. Returns the values removed."""
    from app.services.audit_service import log_audit
    from app.services.product_spec_write import apply_spec_values

    registry = (
        db.query(ProductSpecRegistry)
        .filter(ProductSpecRegistry.spec_key == SEAT_COVER_SPEC_KEY)
        .one_or_none()
    )
    if registry is None:
        return 0
    holding = _spec_rows_holding(db)
    removed = [
        {
            "product_code": product.product_code,
            "company_id": str(product.company_id),
            "value": (spec.values or {}).get(SEAT_COVER_SPEC_KEY, {}).get("value"),
        }
        for spec, product in holding
    ]
    was_active, old_rules = bool(registry.is_active), registry.derivation_rules
    if not removed and not was_active and old_rules == []:
        return 0
    registry.is_active = False
    registry.derivation_rules = []
    db.flush()
    for code in sorted({row["product_code"] for row in removed}):
        apply_spec_values(db, code, [{"spec_key": SEAT_COVER_SPEC_KEY, "op": "revert"}], commit=False)
    log_audit(
        db,
        "product_spec_registry",
        str(registry.id),
        "UPDATE",
        old_values={"is_active": was_active, "derivation_rules": old_rules, "values": removed},
        new_values={"is_active": False, "derivation_rules": [], "values": []},
        description="Seat Cover spec retired: replaced by seat cover compatibility links.",
        event="seat_cover_spec.retire",
    )
    return len(removed)


def _report(the_plan: dict, out: TextIO) -> None:
    for link in sorted(the_plan["links"], key=lambda x: (x["flyer_row"], x["wc_code"], x["cover_code"])):
        tag = ("  (inactive SKU)" if link["inactive"] else "") + (
            "  (already linked)" if link["already"] else ""
        )
        out.write(
            f"LINK  row {link['flyer_row']}  {link['printed_code']} -> {link['cover_code']}"
            f"  SKU {link['wc_code']}{tag}\n"
        )
    for u in sorted(the_plan["unresolved"], key=lambda x: x["wc_code"]):
        out.write(f"UNRESOLVED  {u['wc_code']}  {u['reason']}\n")
    for m in sorted(the_plan["missing"], key=lambda x: (x["flyer_row"], x["printed_code"])):
        sku = f"  SKU {m['wc_code']}" if m["wc_code"] else ""
        out.write(
            f"MISSING  row {m['flyer_row']}  {m['printed_code']} -> {m['cover_code']}{sku}  {m['reason']}\n"
        )


def main(argv: Optional[Sequence[str]] = None, db=None, out: Optional[TextIO] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="write the links (default: dry run)")
    parser.add_argument(
        "--retire-spec",
        action="store_true",
        help="with --apply: remove the old Seat Cover spec values and deactivate the key",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    out = out or sys.stdout

    if args.retire_spec and not args.apply:
        out.write("--retire-spec runs only together with --apply, after the links are written.\n")
        return 2

    owns_session = db is None
    if owns_session:
        from app.database import SessionLocal

        db = SessionLocal()
    try:
        the_plan = plan(db)
        _report(the_plan, out)
        new = sum(1 for link in the_plan["links"] if not link["already"])
        out.write(
            f"\n{len(the_plan['unresolved'])} unresolved SKUs (never linked), "
            f"{len(the_plan['missing'])} missing pairs.\n"
        )
        if not args.apply:
            out.write(f"DRY RUN: {new} links to write. Run with --apply to write them.\n")
            return 0

        written, already = apply(db, the_plan)
        if args.retire_spec:
            blocked = companies_without_flyer_links(db)
            if blocked:
                db.rollback()
                out.write(
                    "Refused: these companies hold Seat Cover spec values but have no flyer "
                    f"link to replace them: {', '.join(blocked)}. Nothing was written.\n"
                )
                return 2
            removed = retire_spec(db)
        db.commit()
        out.write(f"{written} links written, {already} already linked.\n")
        if args.retire_spec:
            out.write(f"{removed} Seat Cover spec value{'' if removed == 1 else 's'} removed.\n")
        return 0
    finally:
        if owns_session:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
