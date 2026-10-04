"""Which brands a WhatsApp contact may see: the one reader of `respond_contacts.brand_ids`.

NULL or an empty array means every brand (unscoped, the answer is `None`). A non-empty list
is a strict allow-list: a product whose brand is not in it, or has no brand at all, is out
of scope (CONTACT-BRAND-SCOPE Q1). Core code: it never imports the chatbot package
(tests/chatbot/test_import_boundary.py).

`contact_id` is the INTERNAL `respond_contacts.id`; a Respond.io id resolves through
`field_access.resolve_contact_with_null_workspace_fallback` first (as
`contact_customer_scope.py` does).

PLAN-contact-brand-scope-4oct.md, slice 1.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models.base import _BRAND_SCOPE_KEY


#: A brand id nothing carries: the scope a lookup that failed falls back to, so every product
#: reads as out of scope (an empty scope would mean "every brand").
NO_BRAND_ID = "00000000-0000-0000-0000-000000000000"


def contact_brand_scope(db: Session, contact_id: str, space_id: Optional[str] = None) -> Optional[frozenset[str]]:
    """The contact's accessible brand ids, or None when it is unscoped."""
    from app.models.access import RespondContact
    from app.models.base import company_scope
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    if not contact_id:
        return None
    key = str(contact_id)
    # Company scope OFF for the read: a NULL-workspace contact's row can be hidden under an
    # API-key request's scope, and a hidden row must not read as "unscoped" by accident.
    with company_scope(db, None):
        resolved = resolve_contact_with_null_workspace_fallback(db, contact_id=key, space_id=space_id) or key
        row = db.query(RespondContact.brand_ids).filter(RespondContact.id == str(resolved)).first()
    ids = row[0] if row else None
    if not ids:
        return None
    return frozenset(str(i) for i in ids)


def brand_predicate(scope: frozenset[str], brand_col):
    """`brand_col IN scope`; a NULL brand never matches (Q1)."""
    return brand_col.in_(sorted(scope))


#: Tools that return money with no product dimension (project and quotation figures), so a
#: brand-scoped contact cannot be given a figure limited to its brands: they do not run for it.
_TOOLS_DENIED_WHEN_SCOPED = frozenset({"crm_project_forecast"})


def brand_scope_allows_tool(tool_name: str, scope: Optional[frozenset[str]]) -> bool:
    """False when `tool_name` must not run for a contact with this brand scope.

    CONTACT-BRAND-SCOPE crew ruling (b): a scoped contact gets no project forecast. This is the
    one seam; the follow-up, option (c), is per-brand project scoping (projects carrying a
    brand), after which this tool can be allowed again with a filtered figure."""
    return not (scope and tool_name in _TOOLS_DENIED_WHEN_SCOPED)


@contextmanager
def _brand_scope_off(db: Session):
    """Run a lookup that must SEE out-of-scope products (the output guard asks which ones are)."""
    had = _BRAND_SCOPE_KEY in db.info
    prev = db.info.pop(_BRAND_SCOPE_KEY, None)
    try:
        yield
    finally:
        if had:
            db.info[_BRAND_SCOPE_KEY] = prev


def out_of_scope_product_codes(db: Session, scope: frozenset[str], codes: Iterable[str]) -> set[str]:
    """The codes whose product brand is NULL or outside `scope`; unknown codes are left alone."""
    from app.models.product import Product

    wanted = {str(c) for c in codes if c}
    if not wanted:
        return set()
    # Case-insensitive: complaint analytics lower-cases its group keys.
    from sqlalchemy import func

    lowered = {c.lower() for c in wanted}
    with _brand_scope_off(db):
        rows = (
            db.query(Product.product_code, Product.brand_id)
            .filter(func.lower(Product.product_code).in_(lowered))
            .all()
        )
    bad = {code.lower() for code, brand_id in rows if brand_id is None or str(brand_id) not in scope}
    return {c for c in wanted if c.lower() in bad}


def out_of_scope_product_ids(db: Session, scope: frozenset[str], ids: Iterable[str]) -> set[str]:
    """As `out_of_scope_product_codes`, for product uuids (a stale pick or follow-up)."""
    from app.models.product import Product

    wanted = set()
    for i in ids:
        try:
            wanted.add(str(uuid.UUID(str(i))))
        except (ValueError, AttributeError):
            continue  # not a product id at all: nothing to look up
    if not wanted:
        return set()
    with _brand_scope_off(db):
        rows = db.query(Product.id, Product.brand_id).filter(Product.id.in_(wanted)).all()
    return {str(pid) for pid, brand_id in rows if brand_id is None or str(brand_id) not in scope}


def product_in_scope_clauses(db: Session, product_id_col) -> list:
    """Filters keeping only rows whose `product_id_col` product is in the session's brand scope.

    For SQL that sums or lists LINES without selecting `Product`, where the session criterion
    cannot see (Q3: totals come from in-scope lines). Empty list when the session is unscoped,
    so an unscoped query is unchanged. The subquery reads the Core table: no recursion into
    the ORM criterion, and a NULL brand fails the IN (Q1)."""
    from sqlalchemy import select

    from app.models.base import get_brand_scope
    from app.models.product import Product

    scope = get_brand_scope(db)
    if not scope:
        return []
    products = Product.__table__
    return [product_id_col.in_(select(products.c.id).where(products.c.brand_id.in_(sorted(scope))))]


# ---------------------------------------------------------------------------
# Settings (the contact page's Brands card and the contacts list column).
# ---------------------------------------------------------------------------


def brand_refs_by_contact(db: Session, contact_ids: Iterable[str]) -> dict[str, list[dict[str, str]]]:
    """`[{id, brand_name}]` per contact (brand name asc), ONE pair of queries for a page.

    A stale id (a deleted brand) names nothing and is left out, and so does a brand of a
    company outside the caller's scope: the contact row is read with company scope off (it is
    not company-owned) but the brand names are read under the caller's own scope."""
    from app.models.access import RespondContact
    from app.models.base import company_scope
    from app.models.product import Brand

    ids = [str(c) for c in contact_ids if c]
    if not ids:
        return {}
    with company_scope(db, None):
        rows = (
            db.query(RespondContact.id, RespondContact.brand_ids)
            .filter(RespondContact.id.in_(ids), RespondContact.brand_ids.isnot(None))
            .all()
        )
        wanted = {str(b) for _cid, brand_ids in rows for b in (brand_ids or [])}
    names = (
        {str(i): n for i, n in db.query(Brand.id, Brand.brand_name).filter(Brand.id.in_(wanted)).all()}
        if wanted
        else {}
    )
    out: dict[str, list[dict[str, str]]] = {}
    for cid, brand_ids in rows:
        refs = [{"id": str(b), "brand_name": names[str(b)]} for b in (brand_ids or []) if str(b) in names]
        if refs:
            out[str(cid)] = sorted(refs, key=lambda r: r["brand_name"].casefold())
    return out


def get_contact_brands(db: Session, contact_id: str) -> dict:
    """`{brand_ids, brands}` for the settings card; 404 for an unknown contact."""
    from app.models.access import RespondContact
    from app.models.base import company_scope
    from app.services.error_handler import handle_not_found

    with company_scope(db, None):
        row = db.query(RespondContact.brand_ids).filter(RespondContact.id == str(contact_id)).first()
    if row is None:
        raise handle_not_found("Respond Contact", contact_id)
    return {
        "brand_ids": [str(b) for b in (row[0] or [])],
        "brands": brand_refs_by_contact(db, [contact_id]).get(str(contact_id), []),
    }


def set_contact_brands(db: Session, contact_id: str, brand_ids: Iterable[str]) -> dict:
    """Replace the contact's accessible brands. `[]` stores NULL (every brand); a brand id that
    does not exist is a 422 and nothing changes."""
    from app.models.access import RespondContact
    from app.models.base import company_scope
    from app.models.product import Brand
    from app.services.error_handler import AppException, handle_not_found

    wanted = list(dict.fromkeys(str(b) for b in brand_ids if b))
    from app.models.base import get_company_scope

    prev_scope = get_company_scope(db)
    with company_scope(db, None):
        contact = db.query(RespondContact).filter(RespondContact.id == str(contact_id)).first()
        if contact is None:
            raise handle_not_found("Respond Contact", contact_id)
        if wanted:
            try:
                for b in wanted:
                    uuid.UUID(b)
            except ValueError:
                raise AppException(422, "Unknown brand", code="UNKNOWN_BRAND") from None
            # Under the caller's own company scope: a brand it cannot see is unknown to it.
            with company_scope(db, prev_scope):
                known = {str(i) for (i,) in db.query(Brand.id).filter(Brand.id.in_(wanted)).all()}
            missing = [b for b in wanted if b not in known]
            if missing:
                raise AppException(422, "Unknown brand", detail=", ".join(missing), code="UNKNOWN_BRAND")
        contact.brand_ids = wanted or None
        db.commit()
    return get_contact_brands(db, contact_id)


def in_scope_product_codes(db: Session, codes: Iterable[str]) -> Optional[set[str]]:
    """The subset of `codes` whose product the session's brand scope allows; None when the
    session is unscoped. Codes that name no product are NOT in scope (a scoped contact is
    shown only what it may see)."""
    from app.models.base import get_brand_scope
    from app.models.product import Product

    if not get_brand_scope(db):
        return None
    wanted = {str(c).strip() for c in codes if c and str(c).strip()}
    if not wanted:
        return set()
    # The session criterion (brand IN scope) is active, so this ORM read returns only
    # in-scope products.
    return {str(c) for (c,) in db.query(Product.product_code).filter(Product.product_code.in_(wanted)).all()}


def complaint_in_scope_clauses(db: Session) -> list:
    """Filters keeping complaints with at least one product line in the session's brand scope
    (none when unscoped). Lines carry the product CODE, so the match is on the code."""
    from sqlalchemy import exists, select

    from app.models.base import get_brand_scope
    from app.models.complaints import Complaint, ComplaintProductLine
    from app.models.product import Product

    scope = get_brand_scope(db)
    if not scope:
        return []
    products = Product.__table__
    in_scope_codes = select(products.c.product_code).where(products.c.brand_id.in_(sorted(scope)))
    return [
        exists().where(
            ComplaintProductLine.complaint_id == Complaint.id,
            ComplaintProductLine.product_code.in_(in_scope_codes),
        )
    ]
