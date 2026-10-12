"""Which product spec keys (Thickness, Material, ...) the chatbot may reveal to a
contact. Three tiers, resolved here and enforced in
``app.services.chatbot.head.access.check_access`` / ``lanes/business/fetch.py``:

    contact override  >  merged market segments  >  global default

Same doctrine as ``app.services.stock_visibility``, copied mechanically where the
shape matches and diverging on the two points the plan calls out
(PLAN-spec-visibility-policy.md "Decisions"):

* The tier axis is MARKET SEGMENT, not contact access type - the owner's own
  vocabulary for this feature is retail/project.
* ``resolve_policy`` FAILS CLOSED TO THE DEFAULT POLICY on an unresolvable
  contact, never to "no answer" - a stock question with nobody to check against
  gets zero rows and no block, but a spec question always gets an answer.

**The floor is code, not data.** The default row is seeded by migration 510, but
a database built by ``create_all`` (CI) has no seeds, so ``default_policy`` falls
back to the ship-closed ``DEFAULT_HIDDEN_KEYS`` floor rather than to "everything
visible". The migration's seed is a literal (migrations stay frozen and do not
import app code); a test pins the two lists equal so they cannot drift apart
silently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from fastapi import status
from sqlalchemy.orm import Session

from app.services.error_handler import AppException

SOURCE_CONTACT = "contact"
SOURCE_SEGMENT = "segment"
SOURCE_DEFAULT = "default"

#: The ship-closed floor (PLAN-spec-visibility-policy.md "Decisions" - "default
#: ships closed"). Migration 510's seed is a literal with the SAME two keys
#: (migrations stay frozen and do not import app code); a test in
#: tests/test_spec_visibility_policy.py pins the seeded list equal to this
#: constant, so the two cannot drift apart silently.
DEFAULT_HIDDEN_KEYS: frozenset[str] = frozenset({"thickness", "board_thickness"})


@dataclass(frozen=True)
class SpecPolicy:
    """What one caller may be told.

    ``spec_keys`` None = every registry key visible; a frozenset = only these
    (empty = none at all). ``excluded_spec_keys`` is the sibling: None = no
    exclusion, a frozenset = hide exactly these (empty = nothing hidden - the
    opposite of what empty means on ``spec_keys``). The two are never both
    non-None on a single ROW (the CHECK), but a MERGED policy across market
    segments can carry both at once: an include list from one segment narrowed
    further by an exclusion from another.
    """

    spec_keys: Optional[frozenset[str]]
    excluded_spec_keys: Optional[frozenset[str]]
    source: str
    source_label: Optional[str] = None


def _row_keys(value) -> Optional[frozenset[str]]:
    if value is None:
        return None
    return frozenset(str(v) for v in value)


def _policy_from_row(row, source: str, source_label: Optional[str] = None) -> SpecPolicy:
    return SpecPolicy(
        spec_keys=_row_keys(row.spec_keys),
        excluded_spec_keys=_row_keys(row.excluded_spec_keys),
        source=source,
        source_label=source_label,
    )


def default_policy(db: Session) -> SpecPolicy:
    """The global default row, or the ship-closed `DEFAULT_HIDDEN_KEYS` floor
    when absent (a database built by `create_all`, CI, has no migration seed -
    S1: this must still be closed, never "everything visible")."""
    from app.models.access import SpecVisibilityPolicy

    row = (
        db.query(SpecVisibilityPolicy)
        .filter(
            SpecVisibilityPolicy.contact_id.is_(None),
            SpecVisibilityPolicy.segment_code.is_(None),
        )
        .first()
    )
    if row is None:
        return SpecPolicy(
            spec_keys=None,
            excluded_spec_keys=frozenset(DEFAULT_HIDDEN_KEYS),
            source=SOURCE_DEFAULT,
        )
    return _policy_from_row(row, SOURCE_DEFAULT)


def contact_override(db: Session, resolved_contact_id: str):
    """The row stored AT the contact tier, or None when the contact inherits."""
    from app.models.access import SpecVisibilityPolicy

    return (
        db.query(SpecVisibilityPolicy)
        .filter(SpecVisibilityPolicy.contact_id == resolved_contact_id)
        .first()
    )


def segment_override(db: Session, segment_code: str):
    """The row stored AT one market segment, or None when that segment inherits."""
    from app.models.access import SpecVisibilityPolicy

    return (
        db.query(SpecVisibilityPolicy)
        .filter(SpecVisibilityPolicy.segment_code == segment_code)
        .first()
    )


def _merge_segment_rows(rows: list[tuple]) -> SpecPolicy:
    """Intersection of Show-only lists, union of Hide-these lists, both carried
    on the merged (in-memory) policy - never on one row. ``rows`` arrives
    pre-ordered by sort order and already narrowed to the segments that carry
    a policy row (the join in ``segment_policy``), so the label is simply the
    FIRST segment by sort order among the rows that carry a policy (plan
    Decisions "Merge")."""
    merged_keys: Optional[frozenset[str]] = None
    for row, _name in rows:
        ids = _row_keys(row.spec_keys)
        if ids is None:
            continue
        merged_keys = ids if merged_keys is None else (merged_keys & ids)

    merged_excluded: Optional[frozenset[str]] = None
    for row, _name in rows:
        ids = _row_keys(row.excluded_spec_keys)
        if ids is None:
            continue
        merged_excluded = ids if merged_excluded is None else (merged_excluded | ids)

    return SpecPolicy(
        spec_keys=merged_keys,
        excluded_spec_keys=merged_excluded,
        source=SOURCE_SEGMENT,
        source_label=rows[0][1],
    )


def segment_policy(db: Session, resolved_contact_id: str) -> Optional[SpecPolicy]:
    """The merged market-segment tier for a contact, or None when no segment the
    contact belongs to carries a row of its own."""
    from app.models.access import (
        MarketSegment,
        SpecVisibilityPolicy,
        respond_contact_market_segments,
    )

    rows = (
        db.query(SpecVisibilityPolicy, MarketSegment.name)
        .join(MarketSegment, MarketSegment.code == SpecVisibilityPolicy.segment_code)
        .join(
            respond_contact_market_segments,
            respond_contact_market_segments.c.segment_code == MarketSegment.code,
        )
        .filter(respond_contact_market_segments.c.contact_id == resolved_contact_id)
        .order_by(MarketSegment.sort_order.asc().nulls_last(), MarketSegment.name.asc())
        .all()
    )
    if not rows:
        return None
    return _merge_segment_rows([(row, name) for row, name in rows])


def resolve_policy(
    db: Session, contact_id: str, space_id: Optional[str] = None
) -> SpecPolicy:
    """The policy that applies to one contact. Contact override beats segments
    beats default.

    ``contact_id`` is accepted in either id space (internal
    ``respond_contacts.id`` or the Respond.io id), with ``space_id``
    disambiguating the latter - the same rule ``stock_visibility.resolve_policy``
    applies. An UNRESOLVABLE contact fails closed to the DEFAULT policy (never to
    "no policy"): a spec question always gets an answer, unlike a stock question,
    which the caller turns into zero rows.
    """
    from app.services.field_access import resolve_contact_id

    resolved = resolve_contact_id(db, contact_id, space_id)
    if not resolved:
        return default_policy(db)

    override = contact_override(db, resolved)
    if override is not None:
        return _policy_from_row(override, SOURCE_CONTACT)

    merged = segment_policy(db, resolved)
    if merged is not None:
        return merged

    return default_policy(db)


def hidden_keys(policy: SpecPolicy, registry_keys: Iterable[str]) -> frozenset[str]:
    """The registry keys this policy hides.

    Show-only null -> nothing hidden except the Hide list; Show-only list ->
    every key not in it, PLUS the Hide list when a merged policy carries both;
    Show-only [] -> every key. A key that has since left the registry ENTIRELY
    is ignored - ``registry_keys`` is the caller's own notion of "still
    exists", which for a READ must be the FULL registry (B1, security review):
    a merely deactivated key stays hidden or stays shown, whichever the stored
    policy already says, never un-hidden by an unrelated `is_active` flip.
    """
    registry = frozenset(str(k) for k in registry_keys)
    hidden: frozenset[str] = frozenset()
    if policy.spec_keys is not None:
        hidden = registry - policy.spec_keys
    if policy.excluded_spec_keys is not None:
        hidden = hidden | (registry & policy.excluded_spec_keys)
    return hidden


def contact_hidden_keys(db: Session, contact_id: str, space_id: Optional[str] = None) -> frozenset[str]:
    """The registry keys hidden from one asking contact, resolved the way `check_access`
    does: the NULL-workspace fallback first (SF-1, a contact with `workspace_id IS NULL`
    would otherwise get the closed DEFAULT policy), then `resolve_policy`, then
    `hidden_keys` over the FULL registry (B1). Shared by the references resolver and the
    `spec_constraints` routes so the two can never disagree about one contact."""
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    resolved = resolve_contact_with_null_workspace_fallback(db, contact_id=contact_id, space_id=space_id)
    policy = resolve_policy(db, resolved or contact_id, space_id)
    return hidden_keys(policy, {key for key, _label in full_registry_rows(db)})


# ------------------------------------------------------------------- admin writes


def validated_spec_keys(db: Session, spec_keys) -> Optional[list[str]]:
    """Reject a key that is not an ACTIVE registry key before it is stored -
    EXCEPT a `DEFAULT_HIDDEN_KEYS` key, which is always accepted (browser
    verification): it is the shipped intent (S1), and its registry row can
    arrive on its own lane's own schedule (`board_thickness`, the sink-
    thickness loader) - a card merely showing the ship-closed default must not
    422 on Save because that OTHER lane has not landed yet.

    Inactive and unknown get the SAME answer otherwise (AC-12): from the
    admin's side, a retired key is not a key they may hide or show any more
    than one that was never registered.
    """
    from app.models.product_spec import ProductSpecRegistry

    if spec_keys is None:
        return None
    wanted = [str(k) for k in spec_keys]
    if not wanted:
        return []

    to_check = [k for k in wanted if k not in DEFAULT_HIDDEN_KEYS]
    active: set[str] = set()
    if to_check:
        active = {
            row_key
            for (row_key,) in db.query(ProductSpecRegistry.spec_key)
            .filter(
                ProductSpecRegistry.spec_key.in_(to_check),
                ProductSpecRegistry.is_active.is_(True),
            )
            .all()
        }
    missing = [k for k in to_check if k not in active]
    if missing:
        raise AppException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=f"Unknown spec key: {', '.join(missing)}",
            code="VALIDATION_ERROR",
        )
    # De-duplicated, order-insensitive: the set is a membership test, never a
    # list the reader sees in this order.
    return sorted(set(wanted))


def reject_both_spec_rules(spec_keys, excluded_spec_keys) -> None:
    """422 before either list is validated (or the DB touched), so the message
    is deterministic rather than depending on which of the two happened to be
    checked first."""
    if spec_keys is not None and excluded_spec_keys is not None:
        message = "Pick specs to show or to hide, not both."
        raise AppException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=message,
            detail=message,
            code="VALIDATION_ERROR",
        )


def require_segment(db: Session, segment_code: str) -> str:
    """The code, or 404. Lives here rather than in the router, which must not
    query the database at all."""
    from app.models.access import MarketSegment
    from app.services.error_handler import handle_not_found

    exists = (
        db.query(MarketSegment.code).filter(MarketSegment.code == segment_code).first()
    )
    if not exists:
        raise handle_not_found("Market segment", segment_code)
    return segment_code


def effective_policy_for_segment(db: Session, segment_code: str) -> SpecPolicy:
    """What a contact belonging ONLY to this segment would get."""
    from app.models.access import MarketSegment

    row = segment_override(db, segment_code)
    if row is None:
        return default_policy(db)
    name = (
        db.query(MarketSegment.name).filter(MarketSegment.code == segment_code).scalar()
    )
    return _policy_from_row(row, SOURCE_SEGMENT, name)


def upsert_policy(
    db: Session,
    *,
    spec_keys,
    excluded_spec_keys,
    contact_id: Optional[str] = None,
    segment_code: Optional[str] = None,
):
    """Create or replace the row AT one tier. Both lists replace wholesale."""
    import uuid as _uuid

    from app.models.access import SpecVisibilityPolicy

    reject_both_spec_rules(spec_keys, excluded_spec_keys)
    spec_keys = validated_spec_keys(db, spec_keys)
    excluded_spec_keys = validated_spec_keys(db, excluded_spec_keys)

    if contact_id:
        row = contact_override(db, contact_id)
    elif segment_code:
        row = segment_override(db, segment_code)
    else:
        row = (
            db.query(SpecVisibilityPolicy)
            .filter(
                SpecVisibilityPolicy.contact_id.is_(None),
                SpecVisibilityPolicy.segment_code.is_(None),
            )
            .first()
        )

    if row is None:
        row = SpecVisibilityPolicy(
            id=str(_uuid.uuid4()), contact_id=contact_id, segment_code=segment_code
        )
        db.add(row)
    # setattr rather than plain assignment: the mapped attribute is typed
    # `Column[...]` at rest, and pyright rejects the direct form.
    setattr(row, "spec_keys", spec_keys)
    setattr(row, "excluded_spec_keys", excluded_spec_keys)
    db.commit()
    db.refresh(row)
    return row


def delete_policy(
    db: Session,
    *,
    contact_id: Optional[str] = None,
    segment_code: Optional[str] = None,
) -> bool:
    """Hard delete of one tier's row. False when the tier already inherits - the
    caller (a route, or the deferred-action handler) turns that into a 404."""
    if contact_id:
        row = contact_override(db, contact_id)
    elif segment_code:
        row = segment_override(db, segment_code)
    else:  # pragma: no cover - the default tier has no DELETE route
        raise ValueError("The default spec visibility policy cannot be deleted.")
    if row is None:
        return False
    # Loaded and deleted through the ORM, not `query.delete()`: a bulk delete
    # never loads the row, so no ORM event fires and the audit listener writes
    # nothing.
    db.delete(row)
    db.commit()
    return True


def active_registry_rows(db: Session) -> list[tuple[str, str]]:
    """`(spec_key, label)` for every ACTIVE registry key, sorted by label - the
    PICKER's vocabulary (`GET /keys`) and WRITE-time validation only. Never for
    computing what is hidden - see `full_registry_rows`."""
    from app.models.product_spec import ProductSpecRegistry

    return (
        db.query(ProductSpecRegistry.spec_key, ProductSpecRegistry.label)
        .filter(ProductSpecRegistry.is_active.is_(True))
        .order_by(ProductSpecRegistry.label.asc())
        .all()
    )


def full_registry_rows(db: Session) -> list[tuple[str, str]]:
    """`(spec_key, label)` for EVERY registry key, active or not - the READ-time
    notion of "still exists" `hidden_keys` needs (B1, security review). A stored
    policy key is the admin's INTENT to hide or show it; a later `is_active`
    flip on the registry (a merchandising decision about a different question -
    whether the key is still offered at all) must not silently un-hide it. Only
    a key whose ROW IS GONE ENTIRELY is "ignored on read" (AC-10)."""
    from app.models.product_spec import ProductSpecRegistry

    return (
        db.query(ProductSpecRegistry.spec_key, ProductSpecRegistry.label)
        .order_by(ProductSpecRegistry.label.asc())
        .all()
    )


def _readable_fallback_label(key: str) -> str:
    """A key with no registry row, rendered the way a person reads it - the
    SAME fallback rule `lib/spec-readable.ts`'s `readable()` uses for a key it
    has no special case for: underscores to spaces, first letter capitalised
    only (`board_thickness` -> "Board thickness", never "Board Thickness").

    Reached only for a key genuinely absent from the registry - a
    `DEFAULT_HIDDEN_KEYS` key whose row has not landed yet (a separate lane,
    e.g. the sink-thickness loader creating `board_thickness`) is the case
    this exists for, but nothing here assumes that; any registry-less key
    reads better this way than as a bare slug.
    """
    words = key.replace("_", " ").strip()
    if not words:
        return key
    return words[0].upper() + words[1:]


def _resolve_refs(db: Session, keys: Optional[frozenset[str]]) -> Optional[list[dict]]:
    """Stored keys resolved to `{key, label}`, sorted by label - the admin card
    renders labels and never a key slug."""
    from app.models.product_spec import ProductSpecRegistry

    if keys is None:
        return None
    if not keys:
        return []
    rows = (
        db.query(ProductSpecRegistry.spec_key, ProductSpecRegistry.label)
        .filter(ProductSpecRegistry.spec_key.in_(list(keys)))
        .all()
    )
    label_map = dict(rows)
    return sorted(
        ({"key": k, "label": label_map.get(k) or _readable_fallback_label(k)} for k in keys),
        key=lambda ref: ref["label"],
    )


def policy_payload(db: Session, policy: SpecPolicy) -> dict:
    """One SpecPolicy as the API returns it.

    `hidden` is computed against the FULL registry (B1), not the active-only
    one: a key the admin already hid or already left visible stays that way
    across an unrelated `is_active` flip.
    """
    registry_rows = full_registry_rows(db)
    registry_keys = frozenset(key for key, _label in registry_rows)
    label_map = dict(registry_rows)
    hidden = hidden_keys(policy, registry_keys)
    hidden_refs = sorted(
        ({"key": k, "label": label_map.get(k) or _readable_fallback_label(k)} for k in hidden),
        key=lambda ref: ref["label"],
    )
    return {
        "specs": _resolve_refs(db, policy.spec_keys),
        "excluded_specs": _resolve_refs(db, policy.excluded_spec_keys),
        "hidden": hidden_refs,
        "source": policy.source,
        "source_label": policy.source_label,
    }
