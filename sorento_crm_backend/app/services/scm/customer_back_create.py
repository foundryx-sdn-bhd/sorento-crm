"""Creating a customer a sales document names that the master has never seen.

The buying-side twin of `supplier_back_create.py`. DEBTOR-CODE-UNIQUE (owner, 4 Oct 2026):
one customer per debtor code per company, like AutoCount, so the match key is the CODE alone
(`customer_identity.customer_by_code`). A known code with a new buyer name returns the existing
customer untouched: the document keeps its own buyer name in `debtor_name`. Only a code nobody
in the company holds creates a row.

Fires only when BOTH code and name are sent (`document_ingest_service`'s caller) - a code-only
miss lands the order unlinked with `debtor_code` written and a warning instead; a new customer
needs a name.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.models.order import Customer
from app.services import customer_identity

logger = logging.getLogger(__name__)


def get_or_create(
    db: Session, *, code: str, name: str, company_id: Optional[str] = None
) -> Optional[Customer]:
    """The customer this debtor code names in the company, created if nobody holds it.

    Company scope: `company_id` (S8 review fix) is an EXPLICIT filter on the lookup, not
    left to ambient ORM scoping alone - another company's row under the same code must never
    be handed back for THIS company's document. The insert itself is stamped by the
    `CompanyScopedMixin` `before_insert` listener from the session's active company, which
    the ingest route has already pinned to the request's anchor.

    Inside a SAVEPOINT, for the same reason `back_create_supplier` uses one: a losing insert
    (a concurrent push creating the same code) must not poison the whole document's
    transaction. The loser re-reads and returns the winner's row.
    """
    existing = customer_identity.customer_by_code(db, code, company_id=company_id)
    if existing is not None:
        return existing
    try:
        with db.begin_nested():
            created = Customer(
                customer_code=code.strip(),
                customer_name=name.strip(),
                customer_type="company",
                is_active=True,
            )
            db.add(created)
            db.flush()
    except (IntegrityError, DataError):
        logger.warning(
            "could not back-create customer %r/%r from a document push "
            "(the code already exists, or the value does not fit the column)",
            code,
            name,
        )
        return customer_identity.customer_by_code(db, code, company_id=company_id)
    return created
