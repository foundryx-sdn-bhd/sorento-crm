"""SO-PO-REF: the one key fold for a customer's PO number, used at write AND lookup.

Upper-case, keep letters and digits only, then drop ONE leading "PONO" or "PO" when
something remains after it. This is the only string handling a PO number gets: no pattern
detection in user text, no fuzzy match.
"""
from __future__ import annotations

from typing import Optional


def po_ref_key(value: Optional[str]) -> Optional[str]:
    folded = "".join(ch for ch in (value or "").upper() if ch.isalnum())
    for prefix in ("PONO", "PO"):
        if folded.startswith(prefix):
            if len(folded) > len(prefix):
                folded = folded[len(prefix):]
            break
    return folded or None
