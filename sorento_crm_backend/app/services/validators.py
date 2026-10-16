"""Shared field validators - single source of truth across portal + system forms.

Keeping these here (not inline per route/service) guarantees portal save and
system save enforce identical rules. See
docs/plans/PLAN-project-value-centralized-validation.md.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from app.services.error_handler import handle_validation_error

# total_project_value column is Numeric(15,2): the absolute value must round to
# less than 10^13, else Postgres raises NumericValueOutOfRange (a raw 500).
PROJECT_VALUE_MAX = Decimal(10) ** 13


def validate_project_value(raw: Any) -> Optional[Decimal]:
    """Validate/normalize a Total Project Value for both portal and system forms.

  - ``None`` / blank -> ``None`` (cleared value, allowed)
  - non-numeric (e.g. ``"800K"``) -> 422 "must be a number"
  - out of Numeric(15,2) range -> 422 "too large"
  - otherwise -> ``Decimal``
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
    if isinstance(raw, Decimal):
        value = raw
    else:
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            raise handle_validation_error("Total project value must be a number.")
    if not value.is_finite():
        raise handle_validation_error("Total project value must be a number.")
    try:
        too_large = abs(value) >= PROJECT_VALUE_MAX
    except ArithmeticError:
        too_large = True
    if too_large:
        raise handle_validation_error(
            "Total project value is too large (max 9,999,999,999,999.99)."
        )
    return value
