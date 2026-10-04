"""`{name}` placeholders in fixed wording: dependency-free, shared by the translation memory
(core, checks a staff edit) and the chatbot label catalog (checks a stored translation)."""
from __future__ import annotations

import re

_TOKEN = re.compile(r"\{(\w+)\}")


def tokens(text: str) -> list[str]:
    """The sorted `{name}` placeholders of `text`."""
    return sorted(_TOKEN.findall(text))


def tokens_match(a: str, b: str) -> bool:
    """Whether two texts carry the same placeholders (same multiset)."""
    return tokens(a) == tokens(b)
