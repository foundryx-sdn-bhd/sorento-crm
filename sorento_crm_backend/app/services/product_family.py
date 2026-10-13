"""CODE-VARIANTS: the one rule that derives a product's model family from its code.

The family is the text before the first dash, whitespace removed, uppercased; None when
that text is empty or holds no digit. `FAMILY_CODE_SQL` is the same rule in SQL (migration
backfill); a test asserts the two agree.
"""
from __future__ import annotations

import re
from typing import Optional

_WS = re.compile(r"\s+")

# Evaluates against a `product_code` column of the enclosing query.
FAMILY_CODE_SQL = (
    "(CASE WHEN upper(regexp_replace(split_part(product_code, '-', 1), '\\s', '', 'g')) ~ '[0-9]' "
    "THEN upper(regexp_replace(split_part(product_code, '-', 1), '\\s', '', 'g')) END)"
)


def family_code_for(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    head = _WS.sub("", code.split("-", 1)[0]).upper()
    if not head or not any(ch.isdigit() for ch in head):
        return None
    return head
