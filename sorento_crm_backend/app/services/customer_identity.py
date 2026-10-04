"""One customer per debtor code per company (DEBTOR-CODE-UNIQUE, owner 4 Oct 2026).

Card: documentation/plans/autocount/CARD-debtor-code-unique-04oct.md.

A customer is identified by company + `lower(btrim(customer_code))`, never by name and never
across companies. Every create path asks `customer_by_code` first and only creates on a miss;
a document's own buyer name stays on the document (`debtor_name`) and never renames the
customer.

Until the merge (`app.services.customer_merge`) has run, a code can still hold several rows.
`customer_by_code` then returns the row the merge will keep, by the same `SURVIVOR_ORDER_SQL`
the merge uses, so every new document already lands on the survivor.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.base import get_company_scope
from app.models.order import Customer, CustomerAlias
from app.services.company_scope import resolve_write_company_id


def code_key(code: Optional[str]) -> str:
    """The identity form of a debtor code: trimmed, lower case."""
    return (code or "").strip().lower()


#: Survivor order inside one (company, code) group, written against a customers alias `c`
#: (card R6, Q1 (a)): a row linked to AutoCount by a code-form ref (`<db>:<code>`, owner rule
#: "linkage by code") first, then any AutoCount-linked row, then the oldest `created_at`,
#: ties to the lowest id.
SURVIVOR_ORDER_SQL = """
    EXISTS (
        SELECT 1 FROM integration_references r
        WHERE r.entity_type = 'customers' AND r.entity_id = c.id::text
          AND lower(btrim(regexp_replace(r.source_ref, '^[^:]*:', ''))) = lower(btrim(c.customer_code))
    ) DESC,
    EXISTS (
        SELECT 1 FROM integration_references r
        WHERE r.entity_type = 'customers' AND r.entity_id = c.id::text
    ) DESC,
    c.created_at ASC NULLS LAST,
    c.id ASC
"""


def customer_by_code(
    db: Session, code: Optional[str], *, company_id: Optional[str] = None
) -> Optional[Customer]:
    """The one customer this debtor code names, or None.

    `company_id` filters explicitly. Without it, the company the caller's next insert would
    be stamped with (`resolve_write_company_id`) is used, so a lookup under an all-companies
    scope (an import job with no company snapshot) never links a document to another
    company's customer under the same code. Only a scope that cannot name one company falls
    back to the session's scope alone (the ORM load below is auto-scoped). Raw SQL picks the
    id so the survivor order can read `integration_references`.
    """
    key = code_key(code)
    if not key:
        return None
    if company_id is None:
        company_id = resolve_write_company_id(get_company_scope(db), ambiguous=None)
    params: dict = {"k": key}
    company_sql = ""
    if company_id is not None:
        company_sql = " AND c.company_id = :cid"
        params["cid"] = str(company_id)
    ids = db.execute(
        text(
            f"""
            SELECT c.id::text FROM customers c
            WHERE lower(btrim(c.customer_code)) = :k{company_sql}
            ORDER BY {SURVIVOR_ORDER_SQL}
            """
        ),
        params,
    ).scalars().all()
    if not ids:
        return None
    # The ORM query re-applies the session's company scope (`do_orm_execute`), so without an
    # explicit `company_id` a row outside the caller's scope is never handed back.
    rows = {str(r.id): r for r in db.query(Customer).filter(Customer.id.in_(ids)).all()}
    for cid in ids:
        if cid in rows:
            return rows[cid]
    return None


def record_alias(
    db: Session,
    customer_id: str,
    name: Optional[str],
    *,
    source: str,
    merge_run_id: Optional[str] = None,
) -> bool:
    """Record `name` as another name of the customer, in the customer's own company.
    Idempotent; the customer's current name is never an alias. Returns True when a row was
    written."""
    clean = (name or "").strip()
    if not clean:
        return False
    row = db.execute(
        text("SELECT company_id::text, customer_name FROM customers WHERE id = :i"),
        {"i": str(customer_id)},
    ).first()
    if row is None:
        return False
    if (row.customer_name or "").strip().lower() == clean.lower():
        return False
    exists = db.execute(
        text(
            "SELECT 1 FROM customer_aliases WHERE customer_id = :i "
            "AND lower(btrim(alias_name)) = :n"
        ),
        {"i": str(customer_id), "n": clean.lower()},
    ).first()
    if exists is not None:
        return False
    db.add(
        CustomerAlias(
            customer_id=str(customer_id),
            company_id=row.company_id,
            alias_name=clean,
            source=source,
            merge_run_id=merge_run_id,
        )
    )
    db.flush()
    return True


#: Every name a customer answers to, as `(customer_id, company_id, name_key)`: its own name
#: and each alias. Joined by equality on `(company_id, name_key)`, which Postgres can hash;
#: an `OR EXISTS(alias)` join predicate cannot be, and measured 3.5 s against 8 ms on a
#: dev-sized copy (review S3).
CUSTOMER_NAMES_SQL = (
    "(SELECT id AS customer_id, company_id, lower(btrim(customer_name)) AS name_key FROM customers "
    "UNION ALL SELECT customer_id, company_id, lower(btrim(alias_name)) FROM customer_aliases)"
)


def outerjoin_customer_by_order_name(query, order_cls):
    """`query.outerjoin(Customer)` on the order's debtor name, matched against the customer's
    name or an alias, within the order's own company (an order names a customer of its own
    company only)."""
    from sqlalchemy import and_, func, select, union_all

    names = union_all(
        select(
            Customer.id.label("customer_id"),
            Customer.company_id.label("company_id"),
            func.lower(func.btrim(Customer.customer_name)).label("name_key"),
        ),
        select(
            CustomerAlias.customer_id.label("customer_id"),
            CustomerAlias.company_id.label("company_id"),
            func.lower(func.btrim(CustomerAlias.alias_name)).label("name_key"),
        ),
    ).subquery("customer_names")
    return query.outerjoin(
        names,
        and_(
            names.c.company_id == order_cls.company_id,
            names.c.name_key == func.lower(func.btrim(order_cls.debtor_name)),
        ),
    ).outerjoin(Customer, Customer.id == names.c.customer_id)
