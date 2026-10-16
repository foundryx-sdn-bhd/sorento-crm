#!/usr/bin/env python3
"""Fill the sales type of existing Sponsorship Forms from their customer's debtor type.

SF-SALES-TYPE (documentation/plans/procurement/PLAN-sf-sales-type-16oct.md): a new SF gets its
sales type when the customer is picked, exactly like a Purchase Request. SFs saved before that
have none. This fills the ones that CAN be filled: an SF linked to a customer (`customer_id`)
whose `sales_type` is blank, from the customer's CURRENT debtor type through the one mapping
(`SALES_TYPE_FOR_DEBTOR_TYPE`: project -> project, project_cash / cash_sales -> cash_sales).

Left alone: SFs with no customer link (free-text customer), SFs that already have a sales type,
every Purchase Request, and SFs whose customer has no debtor type (counted as skipped).

Run from sorento_crm_backend/ (the owner runs it on prod; the PR changes no data):
    python scripts/backfill_sf_sales_type.py            # dry-run, writes nothing
    python scripts/backfill_sf_sales_type.py --apply    # writes, then re-checks

Idempotent: a second `--apply` run fills nothing.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

# Allow `from app.*` imports when invoked from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, or_

from app.database import SessionLocal
from app.models.base import set_company_scope
from app.models.order import Customer
from app.models.procurement import PurchaseRequestHeader
from app.services.debtor_type import SALES_TYPE_FOR_DEBTOR_TYPE


def _candidates(db):
    return (
        db.query(PurchaseRequestHeader, Customer.customer_code, Customer.debtor_type)
        .join(Customer, Customer.id == PurchaseRequestHeader.customer_id)
        .filter(
            PurchaseRequestHeader.request_type == "sponsorship_form",
            or_(
                PurchaseRequestHeader.sales_type.is_(None),
                func.btrim(PurchaseRequestHeader.sales_type) == "",
            ),
        )
        .order_by(PurchaseRequestHeader.request_number)
        .all()
    )


def backfill(db, apply: bool, verbose: bool = False) -> dict:
    """Core logic, isolated from CLI/session plumbing so it is unit-testable.

    Returns `{"candidates", "fill", "skipped_no_debtor_type", "updated"}`: `candidates` is every
    linked SF with a blank sales type; `fill` counts them per sales type they get; a customer
    with no (or an unmapped) debtor type is `skipped_no_debtor_type`; `updated` is the rows
    written (0 on a dry run).
    """
    rows = _candidates(db)
    fill: Counter = Counter()
    skipped = 0
    updated = 0
    for header, customer_code, debtor_type in rows:
        sales_type = SALES_TYPE_FOR_DEBTOR_TYPE.get(debtor_type) if debtor_type else None
        if verbose:
            print(f"  {header.request_number or header.id}  customer {customer_code}  "
                  f"debtor type {debtor_type or '-'}  ->  {sales_type or 'skipped'}")
        if sales_type is None:
            skipped += 1
            continue
        fill[sales_type] += 1
        if apply:
            header.sales_type = sales_type
            updated += 1
    if apply and updated:
        db.commit()
    return {
        "candidates": len(rows),
        "fill": dict(fill),
        "skipped_no_debtor_type": skipped,
        "updated": updated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the change. Default is dry-run.")
    args = parser.parse_args()

    db = SessionLocal()
    # A script has no request and no principal: `None` is the all-companies scope (an UNSET
    # scope is fail-closed and would see no rows at all).
    set_company_scope(db, None)
    try:
        result = backfill(db, apply=args.apply, verbose=True)
        fills = ", ".join(f"{v}: {n}" for v, n in sorted(result["fill"].items())) or "none"
        prefix = "Done." if args.apply else "[dry-run]"
        print(
            f"{prefix} {result['candidates']} customer-linked sponsorship form(s) with no sales type. "
            f"Fill -> {fills}. Skipped (customer has no debtor type): {result['skipped_no_debtor_type']}. "
            f"Written: {result['updated']}."
        )
        if args.apply:
            after = backfill(db, apply=False)
            print(f"Re-check: {sum(after['fill'].values())} still fillable.")
        else:
            print("No writes. Re-run with --apply to write.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
