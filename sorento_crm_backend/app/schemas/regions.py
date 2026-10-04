"""Packing list regions (REGION-PACKING-LIST): the one validator every write path shares."""
from __future__ import annotations

from typing import Optional

#: The region codes a packing list (and a Packing List attachment) may carry.
REGION_VALUES: tuple[str, ...] = ("west", "east")
DEFAULT_REGIONS: tuple[str, ...] = ("west",)


def normalize_regions(value: Optional[list[str]]) -> Optional[list[str]]:
    """None stays None (the caller's own default applies). Otherwise non-empty, every item a
    known code, duplicates dropped with first-seen order kept. Anything else raises ValueError
    so a pydantic validator turns it into a 422."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("regions must hold at least one of: " + ", ".join(REGION_VALUES))
    out: list[str] = []
    for item in value:
        if item not in REGION_VALUES:
            raise ValueError("regions must be one of: " + ", ".join(REGION_VALUES))
        if item not in out:
            out.append(item)
    return out
