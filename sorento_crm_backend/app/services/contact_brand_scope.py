"""Which brands a WhatsApp contact may see: the one reader of `respond_contacts.brand_ids`.

NULL or an empty array means every brand (unscoped, the answer is `None`). A non-empty list
is a strict allow-list APPLIED PER COMPANY (BRAND-SCOPE-PER-COMPANY, owner ruling 6 Oct 2026):
it restricts only the products of a company that owns at least one brand in the list; a
product of any other company passes untouched (Mocha has no brand rows, so a dual Sorento +
Mocha contact scoped to the Sorento brands sees every Mocha product). Within a restricted
company a product whose brand is not in the list, or has no brand at all, is out of scope
(CONTACT-BRAND-SCOPE Q1). `brand_predicate` is the one place the rule is spelled. Core code:
it never imports the chatbot package (tests/chatbot/test_import_boundary.py).

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


def scoped_brand_companies_select(scope: frozenset[str]):
    """Core `SELECT company_id FROM brands WHERE id IN scope`: the companies the scope restricts.

    The Core table, not the ORM entity, so the session's company criterion never applies (the
    stored list is the truth whatever the viewer may see) and `correlate(None)` so a statement
    that joins `brands` itself cannot capture it."""
    from sqlalchemy import select

    from app.models.product import Brand

    brands = Brand.__table__
    return select(brands.c.company_id).where(brands.c.id.in_(sorted(scope))).correlate(None)


def brand_predicate(scope: frozenset[str], brand_col, company_col):
    """The per-company rule for a product row given its brand and company columns (ORM
    attributes or Core columns alike).

    `brand IN scope OR (EXISTS restricted AND company NOT IN restricted)`, with `restricted` =
    `scoped_brand_companies_select(scope)`. A NULL brand never matches the IN (Q1), so inside a
    restricted company it is out. The EXISTS keeps a scope nothing carries (`NO_BRAND_ID`, or a
    list of only deleted brand ids) fail-closed: it restricts no company, so nothing passes."""
    from sqlalchemy import and_, exists, or_

    restricted = scoped_brand_companies_select(scope)
    return or_(
        brand_col.in_(sorted(scope)),
        and_(exists(restricted), company_col.notin_(restricted)),
    )


def scoped_brand_companies(db: Session, scope: frozenset[str]) -> frozenset[str]:
    """The Python twin of `scoped_brand_companies_select` for the output guard, read with the
    company scope OFF (a Mocha-only contact cannot see the Sorento brand row it is scoped to)."""
    from app.models.base import company_scope

    with company_scope(db, None):
        rows = db.execute(scoped_brand_companies_select(scope)).all()
    return frozenset(str(cid) for (cid,) in rows if cid)


def _product_out_of_scope(scope: frozenset[str], restricted: frozenset[str], brand_id, company_id) -> bool:
    """`brand_predicate` evaluated in Python for one product row (the guard helpers)."""
    if brand_id is not None and str(brand_id) in scope:
        return False
    return not restricted or str(company_id) in restricted


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
    """The codes whose product fails `brand_predicate` (a NULL or other brand inside a restricted
    company); unknown codes and products of an unrestricted company are left alone.

    A code is unique per company, not across companies (migration 305), so a dual contact can
    see two rows for one code; the code is out only when EVERY row it can see fails (a Mocha
    product sharing its code with an unbranded Sorento product stays in, as the session
    criterion already shows it)."""
    from app.models.product import Product

    wanted = {str(c) for c in codes if c}
    if not wanted:
        return set()
    # Case-insensitive: complaint analytics lower-cases its group keys.
    from sqlalchemy import func

    lowered = {c.lower() for c in wanted}
    restricted = scoped_brand_companies(db, scope)
    with _brand_scope_off(db):
        rows = (
            db.query(Product.product_code, Product.brand_id, Product.company_id)
            .filter(func.lower(Product.product_code).in_(lowered))
            .all()
        )
    verdicts: dict[str, bool] = {}
    for code, brand_id, company_id in rows:
        key = code.lower()
        out = _product_out_of_scope(scope, restricted, brand_id, company_id)
        verdicts[key] = verdicts.get(key, True) and out
    bad = {key for key, out in verdicts.items() if out}
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
    restricted = scoped_brand_companies(db, scope)
    with _brand_scope_off(db):
        rows = db.query(Product.id, Product.brand_id, Product.company_id).filter(Product.id.in_(wanted)).all()
    return {
        str(pid)
        for pid, brand_id, company_id in rows
        if _product_out_of_scope(scope, restricted, brand_id, company_id)
    }


def product_in_scope_clauses(db: Session, product_id_col) -> list:
    """Filters keeping only rows whose `product_id_col` product is in the session's brand scope.

    For SQL that sums or lists LINES without selecting `Product`, where the session criterion
    cannot see (Q3: totals come from in-scope lines). Empty list when the session is unscoped,
    so an unscoped query is unchanged. The subquery reads the Core table: no recursion into
    the ORM criterion; the rule is `brand_predicate` (per company, NULL brand out, Q1)."""
    from sqlalchemy import select

    from app.models.base import get_brand_scope
    from app.models.product import Product

    scope = get_brand_scope(db)
    if not scope:
        return []
    products = Product.__table__
    in_scope = select(products.c.id).where(brand_predicate(scope, products.c.brand_id, products.c.company_id))
    return [product_id_col.in_(in_scope)]


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
    shown only what it may see).

    A code here carries no company (complaint lines), and a code is unique per company only,
    so it is in scope when at least one product the session can see under it passes
    `brand_predicate` AND none fails: a code shared by a passing Mocha row and an out-of-scope
    Sorento row stays out (fail closed; the complaint cannot say which product it meant)."""
    from app.models.base import get_brand_scope
    from app.models.product import Product

    scope = get_brand_scope(db)
    if not scope:
        return None
    wanted = {str(c).strip() for c in codes if c and str(c).strip()}
    if not wanted:
        return set()
    restricted = scoped_brand_companies(db, scope)
    # Brand scope OFF so the failing twin of a shared code is seen; company scope stays ON,
    # so only the products this contact can see take part.
    with _brand_scope_off(db):
        rows = (
            db.query(Product.product_code, Product.brand_id, Product.company_id)
            .filter(Product.product_code.in_(wanted))
            .all()
        )
    verdicts: dict[str, bool] = {}
    for code, brand_id, company_id in rows:
        passes = not _product_out_of_scope(scope, restricted, brand_id, company_id)
        verdicts[str(code)] = verdicts.get(str(code), True) and passes
    return {code for code, passes in verdicts.items() if passes}


def complaint_in_scope_clauses(db: Session) -> list:
    """Filters keeping complaints with at least one product line in the session's brand scope
    (none when unscoped). Lines carry the product CODE and complaints carry no company, so the
    match is on the code, bounded the way `in_scope_product_codes` bounds it: a code is in
    scope when a product the session's company scope can see under it passes
    `brand_predicate` and none fails (a Core read, so the company bound is spelled here)."""
    from sqlalchemy import exists, false, select

    from app.models.base import get_brand_scope, get_company_scope
    from app.models.complaints import Complaint, ComplaintProductLine
    from app.models.product import Product

    scope = get_brand_scope(db)
    if not scope:
        return []
    products = Product.__table__
    companies = get_company_scope(db)
    if companies is None:
        visible = []
    elif isinstance(companies, frozenset) and companies:
        visible = [products.c.company_id.in_(sorted(companies))]
    else:
        visible = [false()]  # UNSET / empty: fail closed, as the company hook does
    passing_ids = select(products.c.id).where(brand_predicate(scope, products.c.brand_id, products.c.company_id))
    passing_codes = select(products.c.product_code).where(products.c.id.in_(passing_ids), *visible)
    failing_codes = select(products.c.product_code).where(products.c.id.notin_(passing_ids), *visible)
    in_scope_codes = passing_codes.where(products.c.product_code.notin_(failing_codes))
    return [
        exists().where(
            ComplaintProductLine.complaint_id == Complaint.id,
            ComplaintProductLine.product_code.in_(in_scope_codes),
        )
    ]
