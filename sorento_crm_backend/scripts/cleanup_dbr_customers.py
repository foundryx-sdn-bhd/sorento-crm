#!/usr/bin/env python3
"""Remove the invented `DBR-<md5>` customers (DBR-DEBTOR-CLEANUP).

A DO with a blank debtor code used to mint a customer with a `DBR-<10 hex>` code. That is
retired: such a DO keeps its `debtor_name` text and has no customer. This script clears the
rows already minted: it nulls `orders.customer_id` and `stock_asks.customer_id` on them, drops
their Respond.io chat links, then deletes the customers. `orders.debtor_name` is never touched.

A customer holding an `integration_references` row is a real AutoCount debtor and is left alone.
If any OTHER table still points at a DBR customer the run aborts and changes nothing.
A contact left with no customer link at all is UNRESTRICTED in chat, so `--apply` also aborts
while any contact would be left unlinked, unless it is linked to a real account first or
`--allow-unlink` is passed. A dry run never aborts: it reports the blockers and those contacts.
`RELINKS` is an explicit contact -> real customer map: `--apply` inserts those chat links (skipping
any that exist, reporting an unknown customer id as "missing") before the deletes, and a contact
they cover does not count as left unlinked.

Run from sorento_crm_backend/:
    python -m scripts.cleanup_dbr_customers            # dry run, writes nothing
    python -m scripts.cleanup_dbr_customers --apply [--allow-unlink]
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid

# Allow `from app.*` imports when invoked from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.models.order import Customer

# Owner, 8 Oct: the DELUXE HOME CENTRE (KTN) contact moves to 300-D093, 300-D061 (Mocha DELUXE [A/C I]),
# 300-D094 and 300-D095. Explicit ids, no name matching.
RELINKS: dict[str, list[str]] = {
    "e84b7e3c-9a3d-432e-b81b-ab5c5c2cb586": [
        "1aff9ef4-7685-4b0f-9caf-194dd39e1cac",
        "d69fc206-98ae-4fee-b550-bf1938ebe867",
        "229130f3-0a78-46f2-bc98-880a0b362cb5",
        "1fd2f9a9-977e-4c85-b9fb-2abcb2da4f16",
    ]
}

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


def run(db, apply: bool = False, allow_unlink: bool = False, relinks: dict | None = None) -> dict:
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
    relink_rows = []
    for contact_id, customer_ids in (RELINKS if relinks is None else relinks).items():
        for customer_id in customer_ids:
            cust = db.execute(
                text("SELECT id::text, customer_code, customer_name, company_id::text FROM customers WHERE id::text = :id"),
                {"id": customer_id},
            ).first()
            if cust is None:
                status = "missing"
            else:
                has = db.execute(
                    text("SELECT 1 FROM respond_contact_customers WHERE contact_id = :c AND customer_id::text = :k"),
                    {"c": contact_id, "k": customer_id},
                ).first()
                status = "exists" if has else "add"
            relink_rows.append({
                "contact_id": contact_id,
                "customer_id": customer_id,
                "customer_code": cust[1] if cust else None,
                "customer_name": cust[2] if cust else None,
                "company_id": cust[3] if cust else None,
                "status": status,
            })
    relinked_contacts = {r["contact_id"] for r in relink_rows if r["status"] != "missing"}
    # A contact is left unlinked when every one of its links is to a DBR customer (relinks count).
    kept_contacts = relinked_contacts | {
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
        "relinks": relink_rows,
        "blockers": _blockers(db, ids) if ids else [],
        "customers": customers,
        "order_numbers": [o[0] for o in orders],
    }
    if not apply or not ids:
        return result

    if result["blockers"]:
        raise RuntimeError("DBR customers are still referenced, nothing changed: " + "; ".join(result["blockers"]))
    if unlinked and not allow_unlink:
        raise RuntimeError(
            "nothing changed: these contacts would be left with no customer link, and an unlinked contact "
            "is unrestricted in chat. Link them to a real account first, or pass --allow-unlink: "
            + ", ".join(u["contact_id"] for u in unlinked)
        )
    for r in relink_rows:
        if r["status"] == "add":
            db.execute(
                text(
                    "INSERT INTO respond_contact_customers "
                    "(id, contact_id, customer_id, company_id, is_primary, source, linked_by) "
                    "VALUES (:id, :c, :k, :co, false, 'dbr_cleanup', 'dbr_cleanup')"
                ),
                {"id": str(uuid.uuid4()), "c": r["contact_id"], "k": r["customer_id"], "co": r["company_id"]},
            )
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
    parser.add_argument("--allow-unlink", action="store_true", help="Apply even if contacts end up unlinked.")
    parser.add_argument("--apply", action="store_true", help="Write the changes (default is a dry run).")
    args = parser.parse_args()

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        try:
            res = run(db, apply=args.apply, allow_unlink=args.allow_unlink)
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
        print("Relinks:")
        for r in res["relinks"]:
            print(f"  {r['contact_id']} -> {r['customer_code']} | {r['customer_name']} | {r['status']}")
        print("Blockers:", "; ".join(res["blockers"]) or "(none)")
        print("Contacts left unlinked:")
        for u in res["contacts_left_unlinked"]:
            print(f"  {u['contact_id']} (was {u['dbr_customer_name']})")
        if not args.apply and (res["blockers"] or res["contacts_left_unlinked"]):
            print("--apply will refuse while there are blockers or unlinked contacts "
                  "(link the contacts first, or pass --allow-unlink for contacts).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
