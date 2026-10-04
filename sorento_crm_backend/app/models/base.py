"""Shared model mixins + company-scope state helpers.

Deliberately dependency-light: this module imports ONLY SQLAlchemy, never
``app.database`` or any other ``app.*`` model. That lets ``app/database.py``
import the scope sentinel/helpers from here without a circular import (the
event-registration + predicate machinery lives in
``app/services/company_scope.py``, which DOES pull in models).

Multi-company data isolation - see
``documentation/plans/PLAN-multi-company-isolation.md``.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import FrozenSet, Optional, Union

from sqlalchemy import Column, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declared_attr


# --- Four-state scope sentinel -------------------------------------------------
class _Unset:
    """Distinct default sentinel for ``db.info['company_scope']``.

    MUST NOT be ``None``: ``None`` means "all companies" (backward-compat for
    X-API-Key/system callers), so a code path that never ran the resolver falling
    into ``None`` would leak every company. Absent-key / ``UNSET`` is fail-closed
    (0 rows). See PLAN §3 F2 four-state table.
    """

    _instance: "Optional[_Unset]" = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "UNSET"

    def __reduce__(self):  # keep it a singleton across pickling
        return (_unset_singleton, ())


def _unset_singleton() -> "_Unset":
    return UNSET


UNSET = _Unset()

# The value stored on ``db.info``. One of:
#   UNSET             -> resolver never ran (default) -> false() -> 0 rows
#   None              -> no predicate                 -> all companies
#   frozenset({ids})  -> company_id IN (ids)
#   frozenset()       -> false() -> 0 rows (fail-closed)
CompanyScope = Union[_Unset, None, FrozenSet[str]]

_SCOPE_KEY = "company_scope"


def set_company_scope(db, scope: CompanyScope) -> None:
    """Store the resolved scope on the session so ``do_orm_execute`` can read it."""
    db.info[_SCOPE_KEY] = scope


def get_company_scope(db) -> CompanyScope:
    """Read the scope for a session. Absent key => ``UNSET`` (fail-closed)."""
    return db.info.get(_SCOPE_KEY, UNSET)


_BRAND_SCOPE_KEY = "brand_scope"


def set_brand_scope(db, scope: Optional[FrozenSet[str]]) -> None:
    """Stamp the contact's accessible brand ids on the session (None = every brand).

    CONTACT-BRAND-SCOPE: ``do_orm_execute`` adds a ``Product.brand_id IN scope`` criterion
    while a frozenset is set; None / absent adds nothing (unscoped is byte-identical)."""
    if scope:
        db.info[_BRAND_SCOPE_KEY] = frozenset(str(b) for b in scope)
    else:
        db.info.pop(_BRAND_SCOPE_KEY, None)


def get_brand_scope(db) -> Optional[FrozenSet[str]]:
    return db.info.get(_BRAND_SCOPE_KEY)


@contextmanager
def company_scope(db, scope: CompanyScope):
    """Temporarily set the company scope on ``db`` (tests + workers).

    Restores the prior value (or clears to UNSET) on exit so nested scopes and
    reused sessions don't leak state.
    """
    had = _SCOPE_KEY in db.info
    prev = db.info.get(_SCOPE_KEY, UNSET)
    db.info[_SCOPE_KEY] = scope
    try:
        yield db
    finally:
        if had:
            db.info[_SCOPE_KEY] = prev
        else:
            db.info.pop(_SCOPE_KEY, None)


# --- Owned-model mixin ---------------------------------------------------------
class CompanyScopedMixin:
    """Marks an owned business table as partitioned by ``company_id``.

    Every model carrying this mixin is auto-filtered by the ``do_orm_execute``
    event and auto-stamped on insert by the ``before_insert`` event registered in
    ``app.services.company_scope``. The new-table guard (AC-H2) asserts that any
    model with a ``company_id`` column is a subclass of this mixin.

    ``company_id`` is nullable for the backfill window; a later migration flips it
    NOT NULL for every owned table EXCEPT attachments (which stay nullable - a
    null company means a shared form attachment). Models that must remain
    permanently nullable/shared set ``__company_shared__ = True``.

    INTENTIONAL ORM-nullable / PG-NOT-NULL divergence: migration
    ``305_company_composite_unique`` flips ``company_id`` NOT NULL in Postgres for
    every owned table (except attachments), but this column stays
    ``nullable=True`` at the ORM level ON PURPOSE. The test suite builds its
    schema with sqlite ``Base.metadata.create_all`` (no alembic), and owned-row
    fixtures insert before the request-time auto-stamp would fire - a model-level
    NOT NULL would break those inserts. Runtime safety does NOT depend on this
    flag: the ``before_insert`` auto-stamp + ``do_orm_execute`` scope filter
    already guarantee no null-company write escapes on Postgres, where the hard
    constraint lives. Do NOT set ``nullable=False`` here without also fixing every
    owned-inserting test fixture.
    """

    __company_shared__ = False

    @declared_attr
    def company_id(cls):  # noqa: N805
        return Column(
            UUID(as_uuid=False),
            ForeignKey("companies.id"),
            nullable=True,
            index=True,
        )
