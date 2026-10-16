#!/usr/bin/env python3
"""One-off: link cabinets (SRTBF...) to the mirrors and shelves the mirror flyer prints for them.

WHY
---
The flyer "SORENTO UPDATED MIRROR & BATHROOM FURNITURE LIST" (header "UPDATE : 03.08.2026",
10 groups, 42 rows, column "Suitable For Cabinet") is the only source of which mirror
(`SRTMR...`) and which shelf (`SRTSV...`) fits which basin cabinet. Owner decision 14 Oct:
link it once with this script; from then on people keep the links by hand in the product
page's Compatible section (Variants tab). There is no review screen.

WHAT IT DOES
------------
`FLYER_ROWS` below is the flyer as printed: (kind, accessory code, host code), one row per
mirror or shelf per cabinet. For every company that holds at least one code of a row:

* LINK when it holds both codes (exact match after code normalisation, no prefix guessing:
  SRTSV310-DIY and SRTSV334-RED are not flyer shelves), in that company, kind `mirror` or
  `shelf`, source `flyer`. A pair already linked under the same kind (by this script or by a
  person, either orientation) is counted, never duplicated, so a second run writes nothing.
* MISSING when it holds only one code: the code it lacks is reported, nothing is linked.
  A row whose codes no company holds is reported once, with both codes.

DRY-RUN unless --apply: it prints what it would link and writes nothing.

    venv/bin/python scripts/oneoff/link_mirror_shelf_flyer.py
    venv/bin/python scripts/oneoff/link_mirror_shelf_flyer.py --apply

Prod step: run the dry run and read the report, run with --apply, then run the dry run again
(it must say 0 links to write).

Plan: documentation/plans/products/PLAN-mirror-shelf-links-14oct.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional, Sequence, TextIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.models.base import company_scope  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.product_compatibility import ProductCompatibility  # noqa: E402
from app.services.product_alternative_service import _norm_sql, normalize_code  # noqa: E402
from app.services.product_compatibility_service import SOURCE_FLYER  # noqa: E402

#: (kind, accessory code, host code) as printed on the flyer.
FLYER_ROWS: tuple[tuple[str, str, str], ...] = (
    ("mirror", "SRTMR401-BL", "SRTBF31415"),
    ("shelf", "SRTSV401-BL", "SRTBF31415"),
    ("mirror", "SRTMR31417", "SRTBF31417"),
    ("shelf", "SRTSV31417", "SRTBF31417"),
    ("mirror", "SRTMR11706", "SRTBF11706"),
    ("shelf", "SRTSV11706", "SRTBF11706"),
    ("mirror", "SRTMR11404-BL", "SRTBF11404"),
    ("mirror", "SRTMR11404-BL", "SRTBF11405"),
    ("mirror", "SRTMR11404-BL", "SRTBF11707"),
    ("mirror", "SRTMR11404-BL", "SRTBF11705"),
    ("mirror", "SRTMR11404-BL", "SRTBF11412"),
    ("mirror", "SRTMR11404-BL", "SRTBF11413-MBL"),
    ("mirror", "SRTMR11404-BL", "SRTBF11501"),
    ("shelf", "SRTSV11404-BL", "SRTBF11404"),
    ("shelf", "SRTSV11404-BL", "SRTBF11405"),
    ("shelf", "SRTSV11404-BL", "SRTBF11707"),
    ("shelf", "SRTSV11404-BL", "SRTBF11705"),
    ("shelf", "SRTSV11404-BL", "SRTBF11412"),
    ("shelf", "SRTSV11404-BL", "SRTBF11413-MBL"),
    ("shelf", "SRTSV11404-BL", "SRTBF11501"),
    ("mirror", "SRTMR11406-WH", "SRTBF11406"),
    ("mirror", "SRTMR11406-WH", "SRTBF11708"),
    ("mirror", "SRTMR11406-WH", "SRTBF11416"),
    ("mirror", "SRTMR11406-WH", "SRTBF11714"),
    ("mirror", "SRTMR11406-WH", "SRTBF11502"),
    ("shelf", "SRTSV11406-WH", "SRTBF11406"),
    ("shelf", "SRTSV11406-WH", "SRTBF11708"),
    ("shelf", "SRTSV11406-WH", "SRTBF11416"),
    ("shelf", "SRTSV11406-WH", "SRTBF11714"),
    ("shelf", "SRTSV11406-WH", "SRTBF11502"),
    ("mirror", "SRTMR11403", "SRTBF11403"),
    ("shelf", "SRTSV11403", "SRTBF11403"),
    ("mirror", "SRTMR11413", "SRTBF11413"),
    ("mirror", "SRTMR11413", "SRTBF11713"),
    ("shelf", "SRTSV11413", "SRTBF11413"),
    ("shelf", "SRTSV11413", "SRTBF11713"),
    ("mirror", "SRTMR11415", "SRTBF11415"),
    ("shelf", "SRTSV11415", "SRTBF11415"),
    ("mirror", "SRTMR11417", "SRTBF11417"),
    ("shelf", "SRTSV11417", "SRTBF11417"),
    ("mirror", "SRTMR11503", "SRTBF11503"),
    ("shelf", "SRTSV11503", "SRTBF11503"),
)


def plan(db) -> dict:
    """What the flyer says, laid over the whole catalogue (every company). Writes nothing."""
    wanted = sorted({normalize_code(c) for _, a, h in FLYER_ROWS for c in (a, h)})
    links, missing = [], []
    with company_scope(db, None):
        found = {
            (str(p.company_id), normalize_code(p.product_code)): p
            for p in db.query(Product).filter(_norm_sql(Product.product_code).in_(wanted))
        }
        linked = {
            (str(a), str(h), kind)
            for a, h, kind in db.query(
                ProductCompatibility.seat_cover_product_id,
                ProductCompatibility.wc_product_id,
                ProductCompatibility.kind,
            )
        }
    companies = sorted({company for company, _ in found})
    for kind, acc_code, host_code in FLYER_ROWS:
        base = {"kind": kind, "accessory_code": acc_code, "host_code": host_code}
        holders = 0
        for company in companies:
            acc = found.get((company, normalize_code(acc_code)))
            host = found.get((company, normalize_code(host_code)))
            if acc is None and host is None:
                continue
            holders += 1
            if acc is None or host is None:
                absent = [code for code, p in ((acc_code, acc), (host_code, host)) if p is None]
                missing.append({**base, "company_id": company, "not_found": absent})
                continue
            links.append(
                {
                    **base,
                    "company_id": company,
                    "accessory_id": acc.id,
                    "host_id": host.id,
                    # Either orientation counts: a pair already linked the other way round
                    # must not gain a second, opposite row.
                    "already": (str(acc.id), str(host.id), kind) in linked
                    or (str(host.id), str(acc.id), kind) in linked,
                    "inactive": not (acc.is_active and host.is_active),
                }
            )
        if holders == 0:
            missing.append({**base, "company_id": None, "not_found": [acc_code, host_code]})
    return {"links": links, "missing": missing}


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
                    seat_cover_product_id=link["accessory_id"],
                    wc_product_id=link["host_id"],
                    kind=link["kind"],
                    source=SOURCE_FLYER,
                )
            )
            written += 1
        db.flush()
    return written, already


def _report(the_plan: dict, out: TextIO) -> None:
    for link in the_plan["links"]:
        tag = ("  (inactive SKU)" if link["inactive"] else "") + (
            "  (already linked)" if link["already"] else ""
        )
        out.write(f"LINK  {link['kind']}  {link['accessory_code']} -> {link['host_code']}{tag}\n")
    for m in the_plan["missing"]:
        out.write(
            f"MISSING  {m['kind']}  {m['accessory_code']} -> {m['host_code']}"
            f"  not found: {', '.join(m['not_found'])}\n"
        )


def main(argv: Optional[Sequence[str]] = None, db=None, out: Optional[TextIO] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="write the links (default: dry run)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    out = out or sys.stdout

    owns_session = db is None
    if owns_session:
        from app.database import SessionLocal

        db = SessionLocal()
    try:
        the_plan = plan(db)
        _report(the_plan, out)
        new = sum(1 for link in the_plan["links"] if not link["already"])
        out.write(f"\n{len(the_plan['missing'])} rows with a code not found.\n")
        if not args.apply:
            out.write(f"DRY RUN: {new} links to write. Run with --apply to write them.\n")
            return 0
        written, already = apply(db, the_plan)
        db.commit()
        out.write(f"{written} links written, {already} already linked.\n")
        return 0
    finally:
        if owns_session:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
