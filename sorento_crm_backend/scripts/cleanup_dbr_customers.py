#!/usr/bin/env python3
"""Remove the invented `DBR-<md5>` customers (DBR-DEBTOR-CLEANUP).

A DO with a blank debtor code used to mint a customer with a `DBR-<10 hex>` code. That is
retired: such a DO keeps its `debtor_name` text and has no customer. This script clears the
rows already minted: it nulls `orders.customer_id` and `stock_asks.customer_id` on them, drops
their Respond.io chat links, then deletes the customers. `orders.debtor_name` is never touched.

A customer holding an `integration_references` row is a real AutoCount debtor and is left alone.
If any OTHER table still points at a DBR customer the run aborts and changes nothing.

Run from sorento_crm_backend/:
    python -m scripts.cleanup_dbr_customers            # dry run, writes nothing
    python -m scripts.cleanup_dbr_customers --apply
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow `from app.*` imports when invoked from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.models.order import Customer

_DBR_CUSTOMERS = """
    SELECT c.id::text AS id, c.customer_code, c.customer_name, c.company_id::text AS company_id
    FROM customers c
    WHERE c.customer_code ~ '^DBR-[0-9a-f]{10}$'
      AND NOT EXISTS (
        SELECT 1 FROM integration_references r
        WHERE r.entity_type = 'customers' AND r.entity_id = c.id::text
      )
    ORDER BY c.customer_name, c.customer_code
"""

# Tables this script clears itself. Any other FK to customers that still holds a DBR id blocks the run.
_HANDLED = {("orders", "customer_id"), ("stock_asks", "customer_id"), ("respond_contact_customers", "customer_id")}

_REFERRING_FKS = """
    SELECT conrelid::regclass::text AS tbl, a.attname AS col
    FROM pg_constraint k
    JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
    WHERE k.contype = 'f' AND k.confrelid = 'customers'::regclass AND array_length(k.conkey, 1) = 1
"""


def _blockers(db, ids: list[str]) -> list[str]:
    found = []
    for tbl, col in db.execute(text(_REFERRING_FKS)).all():
        bare = tbl.split(".")[-1].strip('"')
        if (bare, col) in _HANDLED:
            continue
        n = db.execute(text(f"SELECT count(*) FROM {tbl} WHERE {col}::text = ANY(:ids)"), {"ids": ids}).scalar()
        if n:
            found.append(f"{tbl}.{col}: {n} row(s)")
    return found


def run(db, apply: bool = False) -> dict:
    """Count (and with `apply`, perform) the cleanup. Returns the counts and the unlinked contacts."""
    customers = [dict(r._mapping) for r in db.execute(text(_DBR_CUSTOMERS))]
    ids = [c["id"] for c in customers]
    names = {c["id"]: c["customer_name"] for c in customers}

    orders = db.execute(
        text("SELECT order_number, customer_id::text FROM orders WHERE customer_id::text = ANY(:ids) ORDER BY order_number"),
        {"ids": ids},
    ).all()
    asks = db.execute(text("SELECT count(*) FROM stock_asks WHERE customer_id::text = ANY(:ids)"), {"ids": ids}).scalar()
    links = db.execute(
        text("SELECT contact_id, customer_id::text FROM respond_contact_customers WHERE customer_id::text = ANY(:ids)"),
        {"ids": ids},
    ).all()
    # A contact is left unlinked when every one of its links is to a DBR customer.
    kept_contacts = {
        r[0]
        for r in db.execute(
            text("SELECT DISTINCT contact_id FROM respond_contact_customers WHERE NOT (customer_id::text = ANY(:ids))"),
            {"ids": ids},
        )
    }
    unlinked = sorted(
        ({"contact_id": cid, "dbr_customer_name": names[cust]} for cid, cust in links if cid not in kept_contacts),
        key=lambda d: (d["dbr_customer_name"], d["contact_id"]),
    )
    result = {
        "dbr_customers": len(customers),
        "orders_nulled": len(orders),
        "stock_asks_nulled": asks,
        "chat_links": len(links),
        "contacts_left_unlinked": unlinked,
        "customers": customers,
        "order_numbers": [o[0] for o in orders],
    }
    if not apply or not ids:
        return result

    blockers = _blockers(db, ids)
    if blockers:
        raise RuntimeError("DBR customers are still referenced, nothing changed: " + "; ".join(blockers))
    # orders.customer_id is NO ACTION in prod, so null it before the delete.
    db.execute(text("UPDATE orders SET customer_id = NULL WHERE customer_id::text = ANY(:ids)"), {"ids": ids})
    db.execute(text("UPDATE stock_asks SET customer_id = NULL WHERE customer_id::text = ANY(:ids)"), {"ids": ids})
    db.execute(text("DELETE FROM respond_contact_customers WHERE customer_id::text = ANY(:ids)"), {"ids": ids})
    for key, obj in list(db.identity_map.items()):
        if key[0] is Customer and str(key[1][0]) in ids:
            db.refresh(obj)
    db.execute(text("DELETE FROM customers WHERE id::text = ANY(:ids)"), {"ids": ids})
    # Raw SQL bypasses the unit of work: detach the now-gone Customer objects (loaded first, so they
    # stay readable) so the commit does not try to refresh them.
    for key, obj in list(db.identity_map.items()):
        if key[0] is Customer and str(key[1][0]) in ids:
            db.expunge(obj)
    db.commit()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the changes (default is a dry run).")
    args = parser.parse_args()

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        try:
            res = run(db, apply=args.apply)
        except RuntimeError as exc:
            db.rollback()
            print(f"ABORTED: {exc}")
            return 1
        print("APPLIED" if args.apply else "DRY RUN (nothing written; pass --apply to write)")
        print(
            f"DBR customers: {res['dbr_customers']}  orders nulled: {res['orders_nulled']}  "
            f"stock asks nulled: {res['stock_asks_nulled']}  chat links: {res['chat_links']}"
        )
        print("DBR customers (code | name | company):")
        for c in res["customers"]:
            print(f"  {c['customer_code']} | {c['customer_name']} | {c['company_id']}")
        print("DO numbers:", ", ".join(res["order_numbers"]) or "(none)")
        print("Contacts left unlinked:")
        for u in res["contacts_left_unlinked"]:
            print(f"  {u['contact_id']} (was {u['dbr_customer_name']})")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
