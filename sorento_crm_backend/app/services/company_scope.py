"""Central, fail-closed company-scope enforcement.

Two SQLAlchemy events, registered ONCE at import time (via
``register_company_scope_listeners()`` called from ``app/main.py`` AND the worker
path) so BOTH the API process and the RQ worker enforce isolation - the worker
never runs ``main.py``'s ``startup_event``, so anything registered only there is
absent in the worker (mirrors ``lookup_write_listener``).

1. ``do_orm_execute`` (Session): injects a per-entity company predicate into
   every SELECT touching a ``CompanyScopedMixin`` model, reading the four-state
   scope from ``session.info['company_scope']``.
2. ``before_insert`` (Mapper): auto-stamps ``company_id`` from a single-company
   scope; rejects an owned insert that has no company to stamp (AC-D4).

Four-state scope (PLAN §3 F2):
  UNSET (default / absent) -> false()            -> 0 rows (fail-closed)
  None                     -> no predicate        -> all companies
  frozenset({ids})         -> company_id IN (ids)
  frozenset()  (empty)     -> false()            -> 0 rows (fail-closed)

Attachments (``__company_shared__``) are nullable/shared: under any non-None
scope the predicate is ``company_id IS NULL OR company_id IN (ids)`` and under
UNSET/empty it is ``company_id IS NULL`` - shared (form) attachments stay visible
by definition (AC-H5).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from sqlalchemy import event, false, or_
from sqlalchemy.orm import Mapper, ORMExecuteState, Session, object_session, with_loader_criteria
from sqlalchemy.sql import ColumnElement

from app.database import Base
from app.models.base import (
    UNSET,
    CompanyScope,
    CompanyScopedMixin,
    company_scope,
    get_company_scope,
    set_company_scope,
)
from app.services.error_handler import AppException

logger = logging.getLogger(__name__)

# Re-export the state helpers so callers can `from app.services.company_scope import ...`.
__all__ = [
    "UNSET",
    "CompanyScope",
    "CompanyScopedMixin",
    "company_scope",
    "get_company_scope",
    "set_company_scope",
    "register_company_scope_listeners",
    "build_company_predicate",
    "admin_listing_company_filter",
    "pending_company_id",
    "stamp_lookup_companies",
]


_INSTALLED = False
_SCOPED_CLASSES = None

# The incumbent company (Sorento), seeded with this fixed id by migration 302 and
# used as the DB DEFAULT by migration 306. A ``None``-scope (system / all-companies,
# e.g. an n8n X-API-Key call with no contact identity) owned-write is stamped with
# this so backward-compat write flows never hit the AC-D4 rejection.
DEFAULT_COMPANY_ID = "00000000-0000-0000-0000-000000000001"

# Test-only escape hatch (default OFF; NEVER set in production). The pre-isolation
# pytest suite seeds owned rows without a company_id and reads them back; those
# inserts would now be rejected by the auto-stamp under a non-single scope. When
# ``tests/conftest.py`` flips this on, an otherwise-rejected owned insert instead
# leaves ``company_id`` NULL (pre-isolation behaviour) so legacy tests behave as
# before. Production keeps this False → AC-D4 rejection stands. The dedicated
# ``test_company_scope`` rejection test clears it so it can still assert the raise.
_TEST_LEAVE_NULL_OWNED_INSERT = False


def _all_scoped_classes():
    """All mapped CompanyScopedMixin classes (cached). Used as the fallback target
    set when a statement hides its entities inside a subquery (e.g. ``.count()``,
    which reports an empty ``all_mappers``)."""
    global _SCOPED_CLASSES
    if _SCOPED_CLASSES is None:
        _SCOPED_CLASSES = [
            m.class_
            for m in Base.registry.mappers
            if isinstance(m.class_, type) and issubclass(m.class_, CompanyScopedMixin)
        ]
    return _SCOPED_CLASSES


def _is_shared(cls) -> bool:
    """Whether ``cls`` keeps a permanently-nullable/shared company_id (attachments)."""
    return bool(getattr(cls, "__company_shared__", False))


def build_company_predicate(cls, scope: CompanyScope) -> Optional[ColumnElement]:
    """Return the WHERE predicate for entity ``cls`` under ``scope`` (or None for
    "no predicate"). Exposed for the four-state unit test (AC-H3).

    Empty scope compiles to ``false()`` - never ``.in_([])`` (that footgun renders
    an always-false ``IN ()`` on some backends but is easy to break)."""
    shared = _is_shared(cls)

    if scope is None:
        return None
    # Everything left is UNSET or a frozenset. UNSET or an empty frozenset is
    # fail-closed: owned -> 0 rows; shared -> only the null (shared) rows.
    if not isinstance(scope, frozenset) or not scope:
        return cls.company_id.is_(None) if shared else false()

    # Non-empty frozenset of company ids.
    ids = list(scope)
    if shared:
        return or_(cls.company_id.is_(None), cls.company_id.in_(ids))
    return cls.company_id.in_(ids)


def admin_listing_company_filter(db, column) -> Optional[ColumnElement]:
    """Manual company filter for the superadmin/admin-only admin listings.

    Used by tables that carry a ``company_id`` but are DELIBERATELY NOT
    ``CompanyScopedMixin`` (``forms``, ``import_logs``, ``audit_logs``,
    ``import_jobs``) - other consumers of those tables (portal / workflow /
    public / worker reads) must keep working under ANY scope, so we never
    globally auto-filter them via ``do_orm_execute``. Instead each staff LISTING
    splices this predicate onto its own query by hand.

    Four-state - intentionally NOT fail-closed (unlike ``build_company_predicate``):
      None                    -> None (no predicate, all companies)
      frozenset({ids})        -> company_id IN (ids) OR company_id IS NULL
      UNSET / empty frozenset -> None (no predicate; role-gated fallback)

    Rationale for the UNSET/empty -> no-filter choice: these listings are already
    gated to superadmin/admin, and a superadmin always resolves to a
    single-company frozenset, so UNSET/empty should not occur here. If it somehow
    does, showing all rows (a role-gated admin can see everything anyway) is safer
    than hiding every row and looking broken. ``company_id IS NULL`` is always
    OR-ed in on the frozenset branch so legacy/unstamped rows stay visible.

    A caller reachable by a NON-admin must not rely on this for isolation: it has
    to refuse UNSET/empty itself first. ``_authorize_log_read`` in
    ``app/api/v1/audit/audit_logs.py`` does that for the per-record audit history
    read, which staff reach with a record's view permission (#1281).
    """
    scope = get_company_scope(db)
    if scope is None:
        return None
    # UNSET or an empty frozenset: no filter (superadmin-gated fallback).
    if not isinstance(scope, frozenset) or not scope:
        return None
    return or_(column.in_(list(scope)), column.is_(None))


# Kill-switch for the runtime enforcement (filter + auto-stamp). Default ON.
# ``COMPANY_SCOPE_ENFORCE=0`` disables both listeners - used ONLY to measure the
# pre-multi-company test baseline. The DB schema (NOT NULL + DEFAULT Sorento from
# migrations 305/306) is unaffected, so disabling this just reverts read/write
# behaviour to "all companies / DB-default company". Never set in production.
# NOTE (CONTACT-BRAND-SCOPE): the per-contact brand criterion lives in the same
# `do_orm_execute` listener, so COMPANY_SCOPE_ENFORCE=0 switches it off too. That flag is a
# measurement-only escape hatch and must never be set on a stack serving real contacts.
_ENFORCE = os.getenv("COMPANY_SCOPE_ENFORCE", "1") != "0"


_RAISE_ON_AMBIGUOUS = object()


def resolve_write_company_id(
    scope: CompanyScope, *, ambiguous: Any = _RAISE_ON_AMBIGUOUS
) -> Optional[str]:
    """The company an owned row being written belongs to, from the session scope.

    Extracted so a RAW-SQL insert into an owned table can stamp the same company the
    ORM `before_insert` hook would. Raw SQL bypasses that hook entirely, so an owned
    row inserted by hand lands with a NULL company_id and is then invisible to every
    scoped read - the failure looks like missing data, not like a missing stamp.

    ``ambiguous`` is what an UNSET / empty / multi-company scope resolves to. It raises
    by default, because a request that cannot name one company must not create an owned
    row. A caller that legitimately has no scope at all - a cron handler, which has no
    request and no principal - passes the company it means explicitly, at its own call
    site, where the choice is visible.
    """
    if isinstance(scope, frozenset) and len(scope) == 1:
        return next(iter(scope))
    # ``None`` = the deliberate system / all-companies principal; see the long note in
    # ``_stamp_company_id`` for why that write lands in the incumbent company.
    if scope is None:
        return DEFAULT_COMPANY_ID
    if ambiguous is not _RAISE_ON_AMBIGUOUS:
        return ambiguous
    if _TEST_LEAVE_NULL_OWNED_INSERT:
        return None
    raise AppException(
        status_code=400,
        message=(
            "Cannot create this record without an active company. "
            "A single active company is required to stamp company_id."
        ),
        code="company_scope_required",
    )


#: Returned by ``_stamp_scope`` for an object the auto-stamp will not touch.
_NO_STAMP = object()


def _stamp_scope(target: Any) -> Any:
    """The scope that governs ``target``'s auto-stamp, or ``_NO_STAMP`` when the
    ``before_insert`` hook would leave ``company_id`` alone: not an owned row, a
    company already set, a shared (attachment) row, or enforcement switched off.

    Shared by the hook itself and by ``pending_company_id`` so the two can never
    disagree about which inserts get stamped.
    """
    if not _ENFORCE:
        return _NO_STAMP
    if not isinstance(target, CompanyScopedMixin):
        return _NO_STAMP
    if getattr(target, "company_id", None) is not None:
        return _NO_STAMP
    if _is_shared(type(target)):
        return _NO_STAMP
    sess = object_session(target)
    return get_company_scope(sess) if sess is not None else UNSET


def pending_company_id(target: Any) -> Optional[str]:
    """The company an about-to-be-inserted row will end up in, resolved EARLY.

    ``before_insert`` stamps ``company_id``, but that is a mapper-level hook and so
    runs after ``before_flush``. A ``before_flush`` listener reading ``company_id``
    off a brand new row therefore sees ``None`` and records the row as belonging to
    no company. For the audit log that is not merely incomplete: the admin listing
    deliberately shows company-less rows to everyone (legacy rows predate the
    column), so a CREATE row snapshotting ``None`` leaks that entity's values to
    every other company.

    Returns ``None`` when nothing would be stamped, INCLUDING when the scope cannot
    name a single company: the stamp itself raises in that case, so the insert never
    happens. Never invent a company that the write path would not have chosen.
    """
    scope = _stamp_scope(target)
    if scope is _NO_STAMP:
        return None
    return resolve_write_company_id(scope, ambiguous=None)


def register_company_scope_listeners() -> None:
    """Install the scope SELECT filter + insert auto-stamp. Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    if not _ENFORCE:
        return

    @event.listens_for(Session, "do_orm_execute")
    def _apply_company_scope(state: ORMExecuteState) -> None:  # noqa: ANN001
        # Only top-level ORM SELECTs. Skip column/relationship sub-loads: the
        # parent row is already scoped and child tables carry their own
        # company_id, so a direct query on them (the IDOR path) is still covered.
        if not state.is_select or state.is_column_load or state.is_relationship_load:
            return

        # CONTACT-BRAND-SCOPE: a brand-scoped contact sees only products of its brands
        # (a NULL brand fails the IN, Q1). Concrete clause for the same cache reason as
        # the company predicate below; absent scope adds nothing (unscoped is unchanged).
        brand_scope = state.session.info.get("brand_scope")
        if brand_scope:
            from app.models.product import Product

            state.statement = state.statement.options(
                with_loader_criteria(Product, Product.brand_id.in_(sorted(brand_scope)), include_aliases=True)
            )

        scope = state.session.info.get("company_scope", UNSET)
        if scope is None:
            return  # all companies - add nothing

        targets = [
            m.class_
            for m in state.all_mappers
            if isinstance(m.class_, type) and issubclass(m.class_, CompanyScopedMixin)
        ]
        if not targets:
            # ``.count()`` and other subquery-wrapped statements report no
            # top-level mappers. Fall back to every scoped entity - with_loader_criteria
            # is a no-op for entities not present in the statement, so this stays
            # correct (just adds unused options to the less-frequent count path).
            targets = _all_scoped_classes()

        for cls in targets:
            predicate = build_company_predicate(cls, scope)
            if predicate is None:
                continue
            # Pass the CONCRETE predicate (not a lambda). A lambda triggers
            # SQLAlchemy's lambda-statement caching, which bakes the first scope's
            # STRUCTURE (false() vs IN vs IS NULL OR IN) and reuses it for other
            # scopes - silently leaking. A concrete clause becomes part of the
            # statement cache key, so each scope structure caches distinctly.
            # include_aliases still adapts the clause to aliased occurrences.
            state.statement = state.statement.options(
                with_loader_criteria(cls, predicate, include_aliases=True)
            )

    @event.listens_for(Mapper, "before_insert")
    def _stamp_company_id(mapper, connection, target) -> None:  # noqa: ANN001
        # Not owned / already stamped / shared (attachments may legitimately be
        # company-less). ``pending_company_id`` reads the same guard, so an audit
        # snapshot taken in before_flush lands on the same company as the stamp.
        scope = _stamp_scope(target)
        if scope is _NO_STAMP:
            return

        # ``None`` = the deliberate system / all-companies principal (a valid
        # X-API-Key call with NO contact_id/space_id - the n8n backward-compat
        # path). Such a caller writing an owned row belongs to the INCUMBENT
        # company (Sorento) - this matches migration 306's DB DEFAULT and the
        # pre-multi-company behaviour where every write went to the single
        # company. Without this, n8n owned-writes (SPO / GRN / packing-list
        # creates that come in with no contact identity) would be rejected
        # → a backward-compat break. Reads under ``None`` already return all
        # companies; writes now land in the incumbent. A Mocha n8n flow that
        # must write Mocha data passes contact identity → single-company scope.
        #
        # UNSET (resolver never ran / unresolved contact) / empty / multi-company is
        # genuinely ambiguous, and raises: never insert an owned row with a null
        # company (AC-D4). ``_TEST_LEAVE_NULL_OWNED_INSERT`` is the test-only escape.
        company_id = resolve_write_company_id(scope)
        if company_id is not None:
            target.company_id = company_id


# --------------------------------------------------------------------------
# Multi-company reply clarity (PLAN-multi-company-reply-clarity-backend.md §2)
# --------------------------------------------------------------------------
def _lookup_row_company_id(row: Any, row_company_id) -> Optional[str]:
    """The company a returned row belongs to, whatever shape the row is in."""
    if row_company_id is not None:
        value = row_company_id(row)
    elif isinstance(row, dict):
        value = row.get("company_id")
    else:
        value = getattr(row, "company_id", None)
    return str(value) if value else None


def stamp_lookup_companies(
    db,
    payload: dict,
    rows,
    *,
    product_ids=None,
    row_company_id=None,
) -> None:
    """Label a list payload per company iff the lookup spans more than one company.

    The company set is ``companies of the products the tool was asked about``
    (read through the SCOPED ORM, so a product the caller cannot see contributes
    nothing) UNION ``companies of the rows returned``. With one company in it
    (the overwhelmingly common case) this returns having touched nothing, so a
    single-company reply stays byte-identical to what it was before.

    With two or more, ONE ``companies`` query resolves the names, every row gets
    ``company_name`` (and ``company_id`` where the row does not already carry it),
    and ``payload["lookup_companies"]`` names every company searched, including
    the ones that returned no row, which is what lets a consumer say "I checked
    Mocha and Sorento" on an empty result.

    ``rows`` may be ORM instances (which already carry ``company_id``), plain
    dicts, or Pydantic models declaring both fields. ``row_company_id`` is an
    optional callable for rows whose company lives somewhere other than
    ``.company_id``.

    A caller scoped to a single company short-circuits before any query at all:
    the union cannot exceed one, so there is nothing to find out.

    Best-effort: labelling is additive, so any failure warns and leaves the
    payload exactly as it was rather than turning a working list into a 500,
    and the queries run in a SAVEPOINT so a failed one cannot poison the
    caller's transaction either.
    """
    try:
        scope = get_company_scope(db)
        if isinstance(scope, frozenset) and len(scope) == 1:
            # A caller who can see exactly one company cannot produce a union
            # bigger than one, whatever was asked for or returned. Answer here,
            # before spending the products query that could only confirm it.
            return

        rows = list(rows or [])

        company_ids: set[str] = set()
        names: dict = {}
        # Fix lane round 2, N3: a brand filter arrives as a `Product.id` subquery
        # (`order_service.narrow_product_ids_by_brand`), used as is, never read out.
        from sqlalchemy.sql import Select

        ids = (
            product_ids
            if isinstance(product_ids, Select)
            else {str(pid) for pid in (product_ids or []) if pid}
        )
        # Both queries run inside a SAVEPOINT. Swallowing the exception is only
        # half of "never fatal": on Postgres a failed statement (a non-UUID
        # product id is the realistic one) aborts the WHOLE transaction, so a
        # bare try/except would leave the rest of the request on a session
        # where every later query dies with "current transaction is aborted".
        # The savepoint rolls the failure back locally instead.
        with db.begin_nested():
            if isinstance(ids, Select) or ids:
                from app.models.product import Product

                company_ids.update(
                    str(cid)
                    for (cid,) in db.query(Product.company_id)
                    .filter(Product.id.in_(ids))
                    .distinct()
                    .all()
                    if cid
                )
            for row in rows:
                cid = _lookup_row_company_id(row, row_company_id)
                if cid:
                    company_ids.add(cid)

            if len(company_ids) <= 1:
                return

            from app.models.company import Company

            names = {
                str(cid): name
                for cid, name in db.query(Company.id, Company.name)
                .filter(Company.id.in_(company_ids))
                .all()
            }

        for row in rows:
            cid = _lookup_row_company_id(row, row_company_id)
            if not cid:
                continue
            name = names.get(cid)
            if isinstance(row, dict):
                row["company_id"] = cid
                row["company_name"] = name
            else:
                # An ORM row already holds the real column; only assign it when
                # it is missing (a Pydantic row built field-by-field), so we
                # never mark a loaded ORM instance dirty over its own value.
                if not getattr(row, "company_id", None):
                    setattr(row, "company_id", cid)
                setattr(row, "company_name", name)

        payload["lookup_companies"] = sorted(
            ({"id": cid, "name": names.get(cid)} for cid in company_ids),
            key=lambda entry: (entry["name"] or "").lower(),
        )
    except Exception:  # noqa: BLE001 - labelling is additive, never fatal
        logger.warning("Could not label a list payload per company", exc_info=True)
