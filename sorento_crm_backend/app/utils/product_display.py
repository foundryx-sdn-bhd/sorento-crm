"""Product code + name display rule (owner, 14 Oct 2026).

Wherever a product's code and name are shown together, the name shows only when it differs
from the code. The comparison ignores case, outer whitespace and inner whitespace runs; a
blank name counts as equal. Twin of ``sorento_crm_frontend/lib/product-display.ts``.
"""
from __future__ import annotations

from typing import Optional


def _norm(s: Optional[str]) -> str:
    return " ".join((s or "").split()).lower()


def same_display_text(a: Optional[str], b: Optional[str]) -> bool:
    """True when both texts are equal under the display rule."""
    return _norm(a) == _norm(b)


def distinct_name(code: Optional[str], name: Optional[str]) -> Optional[str]:
    """The name when it is non-blank and differs from the code, else None."""
    if not _norm(name):
        return None
    return None if same_display_text(code, name) else name


def code_with_name(code: Optional[str], name: Optional[str], sep: str = " - ") -> str:
    """``code + sep + name``, or the code alone when the name equals the code or is blank."""
    n = distinct_name(code, name)
    return (code or "") if n is None else f"{code or ''}{sep}{n}"
