"""Lenient identifier resolver for API filters.

Agent/LLM-driven callers often pass a business reference (e.g. `FJ24041192`
for a shipment, `SKU-001` for a product, `PROMO-ABC` for a promotion)
when the underlying DB column is a UUID. Without resolution, Postgres
raises `InvalidTextRepresentation: invalid input syntax for type uuid`.

`resolve_identifier` converts such a string into a concrete UUID (or list
of UUIDs) by:
  1. Returning it as-is if the value is already a valid UUID.
  2. Doing a case-insensitive exact match on a business-code column.
  3. Falling back to a case-insensitive `ILIKE %value%` search on one or
     more secondary columns (e.g. name) when *fuzzy=True* - useful for
     natural-language filters like `supplier="Acme"`.

Returns `None` when the value is blank. Returns an empty list when the
lookup produced no matches; callers MUST treat that as "no rows match"
rather than unfiltered.
"""
from __future__ import annotations

import uuid as _uuid
from typing import Optional, Sequence, Type

from sqlalchemy import func, or_
from sqlalchemy.orm import Session


def is_uuid(value: Optional[str]) -> bool:
    if value is None:
        return False
    try:
        _uuid.UUID(str(value).strip())
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def resolve_identifier(
    db: Session,
    value: Optional[str],
    model: Type,
    *,
    code_fields: Sequence[str] = (),
    fuzzy_fields: Sequence[str] = (),
    fuzzy: bool = False,
    id_field: str = "id",
    extra_filters: Sequence = (),
) -> Optional[list[str]]:
    """Resolve a user-supplied string to a list of UUID strings.

    Args:
        db: SQLAlchemy session.
        value: Raw string from the API (UUID, business code, or free text).
        model: SQLAlchemy model to look up.
        code_fields: Business-code columns for case-insensitive EXACT match
            (e.g. ``["shipment_number"]`` for ``InboundShipment``).
        fuzzy_fields: Columns for case-insensitive partial match (ILIKE ``%v%``)
            used only when *fuzzy=True*.
        fuzzy: Enable fuzzy name/code lookup fallback.
        id_field: Name of the UUID primary-key column (defaults to ``id``).
        extra_filters: Clauses ANDed onto the business-code lookup, so a code naming several
            rows resolves to one that passes them (e.g. a contact's region filter).

    Returns:
        ``None`` if *value* is blank (meaning "no filter applied"),
        otherwise a list of matching UUID strings (may be empty).
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "all":
        return None

    if is_uuid(s):
        return [s]

    id_col = getattr(model, id_field)

    if code_fields:
        lowered = s.lower()
        code_conds = [func.lower(getattr(model, f)) == lowered for f in code_fields]
        row = db.query(id_col).filter(or_(*code_conds), *extra_filters).first()
        if row:
            return [str(row[0])]

    if fuzzy and fuzzy_fields:
        pattern = f"%{s}%"
        fuzzy_conds = [getattr(model, f).ilike(pattern) for f in fuzzy_fields]
        rows = db.query(id_col).filter(or_(*fuzzy_conds)).limit(50).all()
        return [str(r[0]) for r in rows]

    return []


def resolve_single_identifier(
    db: Session,
    value: Optional[str],
    model: Type,
    *,
    code_fields: Sequence[str] = (),
    id_field: str = "id",
) -> Optional[str]:
    """Convenience wrapper that returns a single resolved UUID (or None)."""
    ids = resolve_identifier(
        db,
        value,
        model,
        code_fields=code_fields,
        fuzzy=False,
        id_field=id_field,
    )
    if ids is None:
        return None
    return ids[0] if ids else None
