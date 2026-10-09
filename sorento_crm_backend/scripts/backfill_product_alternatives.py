#!/usr/bin/env python3
"""Backfill ``product_alternatives`` from the -NEW code rule (PRODUCT-ALTERNATIVES).

One-off pass over every product whose code has a whole ``NEW`` segment, through the same
``reconcile_new_alternatives`` the sync, the product API and the Excel import call, so the
backfill and the live rule can never diverge. Dry run by default: reports the pairs it
would link and writes nothing. Idempotent: a second ``--apply`` adds nothing.

Run from sorento_crm_backend/:
    python scripts/backfill_product_alternatives.py            # dry run
    python scripts/backfill_product_alternatives.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.services.product_alternative_service import backfill_new_alternatives  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the links (default: dry run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        report = backfill_new_alternatives(db, apply=args.apply)
        if args.apply:
            db.commit()
        for old_code, new_code in report.pop("pairs"):
            print(f"{old_code} -> {new_code}")
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
