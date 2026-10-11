#!/usr/bin/env python3
"""Backfill orders.customer_id / orders.transporter_id from free-text debtor/transporter.

PLAN-import-tracking-master-ref-upsert-backfill: the "Import tracking" path
(`import_excel_tracking`) historically wrote `debtor_name` / `debtor_code` /
`transporter` as free-text but never populated the `customer_id` / `transporter_id`
FKs - so debtors/transporters that only ever arrived via import are missing from the
`customers` / `transporters` master tables. This script reconciles every affected
order using the SAME helpers the live create/update path uses.

Run from sorento_crm_backend/ AFTER `alembic upgrade head`:
    python scripts/backfill_order_master_refs.py [--dry-run] [--batch 500]

For every order it find-or-creates the customer (pair-match on
lower(btrim(customer_code)) + lower(btrim(customer_name)), blank code -> no customer)
and the transporter (normalized_name = lower(btrim(transporter))) via
`OrderService._upsert_customer_from_debtor` / `._upsert_transporter_from_text`,
then sets the FK only where it is NULL or points at the wrong master row.

Idempotent: master rows dedupe via their unique indexes; a "set-where-mismatch"
update re-runs safely and also corrects any FK a prior run wrote wrong. A second
run reports zero customer / zero transporter changes.
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow `from app.*` imports when invoked from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.database import SessionLocal
from app.models.order import Order
from app.services.order_service import OrderService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report only; write nothing."
    )
    parser.add_argument(
        "--batch", type=int, default=500, help="Orders per commit batch (default 500)."
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        svc = OrderService(db)

        # Only orders that carry debtor OR transporter text worth resolving.
        orders = (
            db.query(Order)
            .filter(
                (func.coalesce(func.trim(Order.debtor_name), "") != "")
                | (func.coalesce(func.trim(Order.transporter), "") != "")
            )
            .all()
        )
        total = len(orders)
        print(f"Scanning {total} order(s) with debtor/transporter text...")

        # Cache resolved ids per distinct text key so we don't re-query the master
        # tables once per order. Keys mirror the helpers' unique keys.
        cust_cache: dict[tuple[str, str], str | None] = {}
        trans_cache: dict[str, str | None] = {}

        cust_set = 0
        trans_set = 0
        pending = 0

        for order in orders:
            # --- customer ---
            name = (order.debtor_name or "").strip()
            if name:
                code = (order.debtor_code or "").strip()
                key = (name.lower(), code.lower())
                if key not in cust_cache:
                    cust_cache[key] = svc._upsert_customer_from_debtor(name, code or None)
                cid = cust_cache[key]
                if cid and str(order.customer_id or "") != str(cid):
                    if not args.dry_run:
                        order.customer_id = cid
                    cust_set += 1
                    pending += 1

            # --- transporter ---
            traw = (order.transporter or "").strip()
            if traw:
                tkey = traw.lower()
                if tkey not in trans_cache:
                    trans_cache[tkey] = svc._upsert_transporter_from_text(traw)
                tid = trans_cache[tkey]
                if tid and str(order.transporter_id or "") != str(tid):
                    if not args.dry_run:
                        order.transporter_id = tid
                    trans_set += 1
                    pending += 1

            if not args.dry_run and pending >= args.batch:
                db.commit()
                pending = 0

        if args.dry_run:
            db.rollback()
            print(
                f"[dry-run] would set customer_id on {cust_set} order(s), "
                f"transporter_id on {trans_set} order(s). "
                f"Distinct customers touched: {len(cust_cache)}, "
                f"transporters: {len(trans_cache)}. No writes."
            )
        else:
            db.commit()
            print(
                f"Done. Set customer_id on {cust_set} order(s), "
                f"transporter_id on {trans_set} order(s)."
            )
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
