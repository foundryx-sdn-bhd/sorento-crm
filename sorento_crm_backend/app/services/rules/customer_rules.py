"""Customer identity (D13): the (code, name) pair, not the code alone.

The same Sage/AutoCount debtor code legitimately hosts more than one customer
name (e.g. "300-D093" for both "Deluxe Home Center (KTN)" and "Deluxe Home
Center AC (I)"), so a code-only match can silently adopt or rename the wrong
row. `order_service.CustomerService.create_customer` matches on
`lower(btrim(code)), lower(btrim(name))` - the same key as the
`uq_customers_company_code_name_lower` composite unique index. The masters
ingest (`master_ingest_service._customer_by_code`, DEBTOR-CODE-LINK, owner
4 Oct 2026) matches a pushed debtor by code and uses this same pair only to
prefer the exact-name row among same-code rows.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.order import Customer
from app.services.scm import customer_back_create


def customer_identity(code: str, name: str) -> tuple[str, str]:
    """Lower-trimmed (code, name) pair used to match/adopt a customer row."""
    return ((code or "").strip().lower(), (name or "").strip().lower())


def fold_market_segment(db: Session, code: Optional[str]) -> Optional[str]:
    """A market segment spelling folded onto a real `market_segments.code`, or
    dropped (D16/S2, moved from `customer_import_service._resolve_market_segments`
    so the ESB masters push and the customer importer never drift on this rule).

    `market_segment_code` is a foreign key, so an unrecognised value would fail
    the whole customer on insert - losing a row over one optional column. The
    caller drops the value and warns `segment_unknown` instead (the same "name
    it, do not guess" rule the importer's unmapped-header report follows).
    Case/whitespace-insensitive, matching the importer's own `_key`.
    """
    if not code or not code.strip():
        return None
    from app.models.access import MarketSegment

    row = (
        db.query(MarketSegment.code)
        .filter(func.lower(func.btrim(MarketSegment.code)) == code.strip().lower())
        .first()
    )
    return row[0] if row else None


def back_create_customer(
    db: Session,
    *,
    code: str,
    name: str,
    segment: Optional[str] = None,
    region: Optional[str] = None,
    company_id: Optional[str] = None,
) -> Optional[Customer]:
    """Wraps `customer_back_create.get_or_create` (D8/D16): both channels
    that back-create a customer off a document - the ESB's `MasterRefResolver`
    and the outstanding SO upload - go through this one function, so a
    segment/region carried on the source document lands the same way from
    either.

    Fill-only (D16): `segment`/`region` are written ONLY when the resolved
    row does not already hold one - true for a freshly created row by
    definition, and for an existing match it is exactly the "never overwrite
    a hand-set segment" rule the masters push also follows.

    `segment` folds through the SAME `fold_market_segment` the masters push
    uses (review B5) - it used to be written STRAIGHT onto
    `market_segment_code`, a foreign key, so an unrecognised spelling would
    have failed on flush rather than dropping quietly the way every other
    entry point into this column already does. An unresolved spelling is
    silently dropped here (no document/verdict for THIS function's own
    caller to warn on - `document_ingest_service._apply_customer_segment_and_region`
    is the caller that has one, and folds `customer_segment` itself before
    ever reaching here).
    """
    customer = customer_back_create.get_or_create(db, code=code, name=name, company_id=company_id)
    if customer is None:
        return None
    if segment and not customer.market_segment_code:
        canonical = fold_market_segment(db, segment)
        if canonical:
            customer.market_segment_code = canonical
    if region and not customer.region:
        customer.region = region
    return customer
