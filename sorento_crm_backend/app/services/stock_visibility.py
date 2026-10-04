"""Which locations a chatbot contact may be told about, and in which shape.

Three tiers, resolved here and enforced in ``StockService.list_stock``:

    contact override  >  contact access type  >  global default

Same doctrine as ``app.services.field_access``: the preflight endpoint
(``GET /inventory/stock-visibility/effective``) is a CONVENIENCE for n8n, never
the mechanism. Forgetting to call it must not be the difference between safe and
leaking, so the data endpoint applies the policy itself.

**Most restrictive wins across access types.** A contact tagged both `dealer`
(availability, dealer pool) and `end_user` (detailed, everywhere) must come out
as a dealer: modes rank ``availability > compact > detailed`` and warehouse sets
INTERSECT, with NULL read as "all" so it never widens anybody. The looser tag
otherwise undoes the dealer rule the moment somebody adds a second label.

**Fail closed on an unresolvable contact.** ``resolve_policy`` answers ``None``,
which the caller turns into zero rows and NO visibility block - exactly what
company scope already does when contact params name nobody. Returning the
default instead would answer a stranger's question with everyone's data.

**The floor is code, not data.** The default row is seeded by migration 416, but
a database built by ``create_all`` (CI) has no seeds, so ``default_policy``
falls back to detailed / all warehouses rather than to nothing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: The whole vocabulary, loosest first. Nothing outside this reaches the table
#: (CHECK constraint) or the API (Literal on the request schema).
MODES: tuple[str, ...] = ("detailed", "compact", "availability")

#: Higher = more restrictive. Drives the merge when a contact holds several
#: access types.
_MODE_RANK = {mode: rank for rank, mode in enumerate(MODES)}

DEFAULT_MODE = "detailed"

SOURCE_CONTACT = "contact"
SOURCE_ACCESS_TYPE = "access_type"
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class Policy:
    """What one caller may be told.

    ``warehouse_ids`` None = every active warehouse their company scope allows;
    an empty frozenset = none at all. ``source_label`` carries the access type's
    NAME (not its code) so the admin badge can read "Access type: Dealer".

    ``excluded_warehouse_ids`` is the include list's sibling (PLAN
    stock-visibility-exclude-locations): None = no exclusion, a frozenset = every
    active warehouse EXCEPT these - including one created after the policy was
    saved. The two are never both non-None on a single row (the CHECK), but a
    MERGED policy across access types can carry both at once (AC-5): an include
    list from one type narrowed further by an exclusion from another.

    ``hide_zero_locations`` drops the locations holding NONE of the product:
    the row in ``detailed``, the location line in ``compact`` (the total is
    unchanged), and nothing at all in ``availability``, which has no line to
    drop. Negatives survive both - a count that cannot be true is an anomaly
    somebody has to act on, not "none left".
    """

    mode: str
    warehouse_ids: Optional[frozenset[str]]
    source: str
    source_label: Optional[str] = None
    hide_zero_locations: bool = False
    excluded_warehouse_ids: Optional[frozenset[str]] = None


def _all_companies(db: Session):
    """Read `warehouses` across every company, for the POLICY's own id list only.

    `stock_visibility_policies` is not company-scoped and its `warehouse_ids` can
    legitimately name locations in two companies. `Warehouse` IS scoped, so the
    admin card - which resolves those ids to `CODE - name` - showed a Sorento
    admin only the Sorento half, and Save writes the list wholesale: one Save on
    an untouched card silently deleted the rest. The stock-balance enforcement
    path stays scoped; this bypass covers the read and the validation of the
    stored list, nothing else.
    """
    from app.models.base import company_scope

    return company_scope(db, None)


def _row_warehouse_ids(row) -> Optional[frozenset[str]]:
    if row.warehouse_ids is None:
        return None
    return frozenset(str(w) for w in row.warehouse_ids)


def _row_excluded_ids(row) -> Optional[frozenset[str]]:
    if row.excluded_warehouse_ids is None:
        return None
    return frozenset(str(w) for w in row.excluded_warehouse_ids)


def _policy_from_row(row, source: str, source_label: Optional[str] = None) -> Policy:
    return Policy(
        mode=row.mode,
        warehouse_ids=_row_warehouse_ids(row),
        source=source,
        source_label=source_label,
        hide_zero_locations=bool(row.hide_zero_locations),
        excluded_warehouse_ids=_row_excluded_ids(row),
    )


def default_policy(db: Session) -> Policy:
    """The global default row, or the inert detailed/all policy when absent."""
    from app.models.access import StockVisibilityPolicy

    row = (
        db.query(StockVisibilityPolicy)
        .filter(
            StockVisibilityPolicy.contact_id.is_(None),
            StockVisibilityPolicy.access_type_code.is_(None),
        )
        .first()
    )
    if row is None:
        return Policy(mode=DEFAULT_MODE, warehouse_ids=None, source=SOURCE_DEFAULT)
    return _policy_from_row(row, SOURCE_DEFAULT)


def contact_override(db: Session, resolved_contact_id: str):
    """The row stored AT the contact tier, or None when the contact inherits."""
    from app.models.access import StockVisibilityPolicy

    return (
        db.query(StockVisibilityPolicy)
        .filter(StockVisibilityPolicy.contact_id == resolved_contact_id)
        .first()
    )


def access_type_override(db: Session, access_type_code: str):
    """The row stored AT one access type, or None when that type inherits."""
    from app.models.access import StockVisibilityPolicy

    return (
        db.query(StockVisibilityPolicy)
        .filter(StockVisibilityPolicy.access_type_code == access_type_code)
        .first()
    )


def _merge_access_type_rows(rows: list[tuple[Any, Optional[str]]]) -> Policy:
    """Most restrictive mode, intersection of includes (NULL = all), UNION of
    exclusions, and hiding wins over showing.

    ``hide_zero_locations`` is OR-ed for the same reason the mode is ranked: a
    contact tagged both `dealer` (hide) and `end_user` (show) must not have the
    stricter rule undone the day somebody adds the second tag. Exclusions merge
    the same direction: two access types withholding different warehouses
    withhold the UNION of both (AC-4), never just the overlap.
    """
    mode = max((row.mode for row, _ in rows), key=lambda m: _MODE_RANK.get(m, 0))
    hide_zero = any(bool(row.hide_zero_locations) for row, _ in rows)

    merged: Optional[frozenset[str]] = None
    for row, _ in rows:
        ids = _row_warehouse_ids(row)
        if ids is None:
            continue
        merged = ids if merged is None else (merged & ids)

    merged_excluded: Optional[frozenset[str]] = None
    for row, _ in rows:
        ids = _row_excluded_ids(row)
        if ids is None:
            continue
        merged_excluded = ids if merged_excluded is None else (merged_excluded | ids)

    # The label names the row that decided the MODE; with several at the same
    # rank the lowest code wins, so the badge is stable rather than order-dependent.
    deciding = sorted(
        (pair for pair in rows if pair[0].mode == mode),
        key=lambda pair: str(pair[0].access_type_code),
    )[0]
    return Policy(
        mode=mode,
        warehouse_ids=merged,
        source=SOURCE_ACCESS_TYPE,
        source_label=deciding[1],
        hide_zero_locations=hide_zero,
        excluded_warehouse_ids=merged_excluded,
    )


def access_type_policy(db: Session, resolved_contact_id: str) -> Optional[Policy]:
    """The merged access-type tier for a contact, or None when no type has a row."""
    from app.models.access import (
        ContactAccessType,
        StockVisibilityPolicy,
        respond_contact_access_types,
    )

    rows = (
        db.query(StockVisibilityPolicy, ContactAccessType.name)
        .join(
            ContactAccessType,
            ContactAccessType.code == StockVisibilityPolicy.access_type_code,
        )
        .join(
            respond_contact_access_types,
            respond_contact_access_types.c.access_type_code == ContactAccessType.code,
        )
        .filter(respond_contact_access_types.c.contact_id == resolved_contact_id)
        .all()
    )
    if not rows:
        return None
    return _merge_access_type_rows([(row, name) for row, name in rows])


def resolve_policy(
    db: Session, contact_id: str, space_id: Optional[str] = None
) -> Optional[Policy]:
    """The policy that applies to one contact, or None when nobody resolved.

    ``contact_id`` is accepted in either id space (internal ``respond_contacts.id``
    or the Respond.io id), with ``space_id`` disambiguating the latter - the same
    rule ``field_access.resolve_contact_id`` already applies, and guessing wrong
    would answer with a stranger's policy.
    """
    from app.services.field_access import resolve_contact_id

    resolved = resolve_contact_id(db, contact_id, space_id)
    if not resolved:
        return None

    override = contact_override(db, resolved)
    if override is not None:
        return _policy_from_row(override, SOURCE_CONTACT)

    merged = access_type_policy(db, resolved)
    if merged is not None:
        return merged

    return default_policy(db)


def require_access_type(db: Session, access_type_code: str) -> str:
    """The code, or 404. Lives here rather than in the router, which must not
    query the database at all.

    404 rather than a dangling row: the FK would refuse it anyway, and a 500
    tells the admin nothing about which code was wrong.
    """
    from app.models.access import ContactAccessType
    from app.services.error_handler import handle_not_found

    exists = (
        db.query(ContactAccessType.code)
        .filter(ContactAccessType.code == access_type_code)
        .first()
    )
    if not exists:
        raise handle_not_found("Contact access type", access_type_code)
    return access_type_code


def effective_policy_for_access_type(db: Session, access_type_code: str) -> Policy:
    """What a contact holding ONLY this access type would get."""
    from app.models.access import ContactAccessType

    row = access_type_override(db, access_type_code)
    if row is None:
        return default_policy(db)
    name = (
        db.query(ContactAccessType.name)
        .filter(ContactAccessType.code == access_type_code)
        .scalar()
    )
    return _policy_from_row(row, SOURCE_ACCESS_TYPE, name)


# ------------------------------------------------------------------- admin writes


def validated_warehouse_ids(db: Session, warehouse_ids) -> Optional[list[str]]:
    """Reject an id that is not a warehouse before it is stored.

    A dangling id would silently narrow the policy to fewer locations than the
    admin picked, and nothing on the card could show them why.

    Malformed and unknown get the SAME answer. `warehouse_ids` is a list of
    strings on the wire, so a value that is not a UUID reaches Postgres as one
    and comes back as a 500 the admin can do nothing with; from where they sit
    it is simply not a warehouse.
    """
    import uuid as _uuid

    from app.models.inventory import Warehouse
    from app.services.error_handler import handle_unprocessable

    if warehouse_ids is None:
        return None
    wanted = [str(w) for w in warehouse_ids]
    if not wanted:
        return []

    malformed: list[str] = []
    parseable: list[str] = []
    for value in wanted:
        try:
            _uuid.UUID(value)
        except (ValueError, AttributeError, TypeError):
            malformed.append(value)
        else:
            parseable.append(value)

    found = set()
    if parseable:
        # Unscoped on purpose: the policy row is NOT company data, but
        # `warehouses` is company-scoped, so a scoped lookup would report a
        # perfectly real warehouse from another company as unknown - and the
        # matching read (`policy_warehouses`) would drop it from the card, so
        # the next Save would quietly delete it from the policy.
        with _all_companies(db):
            found = {
                str(row_id)
                for (row_id,) in db.query(Warehouse.id)
                .filter(Warehouse.id.in_(parseable))
                .all()
            }
    missing = malformed + [w for w in parseable if w not in found]
    if missing:
        raise handle_unprocessable(
            f"Unknown warehouse: {', '.join(missing)}"
        )
    # De-duplicated, order-insensitive: the set is a membership test, never a list
    # the reader sees in this order.
    return sorted(found)


def reject_both_location_rules(warehouse_ids, excluded_warehouse_ids) -> None:
    """422 before either list is validated (or the DB touched), so the message
    is deterministic rather than depending on which of the two happened to be
    checked first. Same `handle_unprocessable` path as "Unknown warehouse" -
    `extractApiError` reads both `detail` and `message`, so the toast is the
    same either way."""
    from app.services.error_handler import handle_unprocessable

    if warehouse_ids is not None and excluded_warehouse_ids is not None:
        raise handle_unprocessable(
            "Pick locations to include or to exclude, not both."
        )


def warehouse_criterion(policy: Optional[Policy], column):
    """The SQLAlchemy filter a policy's location rule reduces to.

    `sa.true()` when the policy carries neither list (every active warehouse
    the caller's company scope already allows), `sa.false()` for an empty
    include list (AC-12's "no stock at all"), `column.in_(...)` for a named
    include list, `column.notin_(...)` for an exclusion, and the two ANDed
    together on a merged policy that carries both (AC-5: an access type's
    include list narrowed further by another's exclusion).
    """
    import sqlalchemy as sa

    if policy is None:
        return sa.true()

    clauses = []
    if policy.warehouse_ids is not None:
        if not policy.warehouse_ids:
            return sa.false()
        clauses.append(column.in_(list(policy.warehouse_ids)))
    if policy.excluded_warehouse_ids is not None:
        # `[]` excludes nothing (AC-12: it means "every active warehouse"), so
        # `notin_([])` - always true - is exactly right without a special case.
        clauses.append(column.notin_(list(policy.excluded_warehouse_ids)))

    if not clauses:
        return sa.true()
    return sa.and_(*clauses)


def visible_location_codes(
    db: Session, contact_id: str, space_id: Optional[str] = None
) -> Optional[set[str]]:
    """The location codes a contact may be told, for a read that names locations outside
    the stock balance (the on-order rung's `location`, STOCK-TOTAL-OS-SCOPE).

    None = every location (the policy names neither list). A set = only these codes: the
    active warehouses `warehouse_criterion` allows, the same set the stock lines and the
    `stock_visibility.warehouse_codes` echo are built from. `availability` names no
    location at all, and an unresolvable contact fails closed, so both are the empty set.
    """
    import sqlalchemy as sa

    from app.models.inventory import Warehouse

    policy = resolve_policy(db, contact_id, space_id)
    if policy is None or policy.mode == "availability":
        return set()
    if policy.warehouse_ids is None and policy.excluded_warehouse_ids is None:
        return None
    rows = (
        db.query(Warehouse.warehouse_code)
        .filter(Warehouse.is_active.is_(sa.true()), warehouse_criterion(policy, Warehouse.id))
        .all()
    )
    return {str(code) for (code,) in rows if code}


def upsert_policy(
    db: Session,
    *,
    mode: str,
    warehouse_ids,
    excluded_warehouse_ids=None,
    hide_zero_locations: bool = False,
    contact_id: Optional[str] = None,
    access_type_code: Optional[str] = None,
):
    """Create or replace the row AT one tier. Both lists replace wholesale.

    Validated HERE rather than at each of the three routes: `reject_both_location_rules`
    runs first, before the DB is touched at all, then each raw list is resolved through
    `validated_warehouse_ids` - one place for every writer instead of three copies of the
    same two calls (and the one place where the deterministic-message check can run
    before either list's own validation, regardless of which the route happened to build
    first).
    """
    import uuid as _uuid

    from app.models.access import StockVisibilityPolicy

    reject_both_location_rules(warehouse_ids, excluded_warehouse_ids)
    warehouse_ids = validated_warehouse_ids(db, warehouse_ids)
    excluded_warehouse_ids = validated_warehouse_ids(db, excluded_warehouse_ids)

    if contact_id:
        row = contact_override(db, contact_id)
    elif access_type_code:
        row = access_type_override(db, access_type_code)
    else:
        row = (
            db.query(StockVisibilityPolicy)
            .filter(
                StockVisibilityPolicy.contact_id.is_(None),
                StockVisibilityPolicy.access_type_code.is_(None),
            )
            .first()
        )

    if row is None:
        row = StockVisibilityPolicy(
            id=str(_uuid.uuid4()),
            contact_id=contact_id,
            access_type_code=access_type_code,
        )
        db.add(row)
    # setattr rather than plain assignment: the mapped attribute is typed
    # `Column[...]` at rest, and pyright rejects the direct form.
    setattr(row, "mode", mode)
    setattr(row, "warehouse_ids", warehouse_ids)
    setattr(row, "excluded_warehouse_ids", excluded_warehouse_ids)
    setattr(row, "hide_zero_locations", bool(hide_zero_locations))
    db.commit()
    db.refresh(row)
    return row


def delete_policy(
    db: Session,
    *,
    contact_id: Optional[str] = None,
    access_type_code: Optional[str] = None,
) -> bool:
    """Hard delete of one tier's row. Missing is not an error - the tier already
    inherits, which is the state the caller asked for."""
    if contact_id:
        row = contact_override(db, contact_id)
    elif access_type_code:
        row = access_type_override(db, access_type_code)
    else:  # pragma: no cover - the default tier has no DELETE route
        raise ValueError("The default policy row cannot be deleted.")
    if row is None:
        return False
    # Loaded and deleted through the ORM, not `query.delete()`: a bulk delete
    # never loads the row, so no ORM event fires and the audit listener writes
    # nothing. Removing a policy would then be the one change to this table with
    # no history at all.
    db.delete(row)
    db.commit()
    return True


def policy_warehouses(db: Session, warehouse_ids: Optional[frozenset[str]]):
    """Resolve stored ids to `{id, code, name}`, ordered by code.

    Resolved server-side so the card renders `CODE - name` without a second round
    trip and without a UUID ever reaching the screen.
    """
    from app.models.inventory import Warehouse

    if warehouse_ids is None:
        return None
    if not warehouse_ids:
        return []
    # Across every company - see `_all_companies`. The policy row is not company
    # data, and a half-resolved list is one Save away from becoming a real one.
    with _all_companies(db):
        rows = (
            db.query(Warehouse)
            .filter(Warehouse.id.in_(list(warehouse_ids)))
            .order_by(Warehouse.warehouse_code.asc())
            .all()
        )
    from app.services.company_scope_resolver import company_name_map

    names = company_name_map(db)
    return [
        {
            "id": str(row.id),
            "code": row.warehouse_code,
            "name": row.warehouse_name,
            "company_name": names.get(str(row.company_id)),
        }
        for row in rows
    ]


def policy_payload(db: Session, policy: Policy) -> dict:
    """One Policy as the API returns it."""
    return {
        "mode": policy.mode,
        "warehouses": policy_warehouses(db, policy.warehouse_ids),
        "excluded_warehouses": policy_warehouses(db, policy.excluded_warehouse_ids),
        "hide_zero_locations": policy.hide_zero_locations,
        "source": policy.source,
        "source_label": policy.source_label,
    }
