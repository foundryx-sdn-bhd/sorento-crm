"""Value and timestamp printers shared by the reply composers.

`timestamp` / `value` / `EM_DASH` / `ISO_RE` moved byte for byte out of
`lanes/business/fetch.py`, which keeps its old names as aliases.
"""
from __future__ import annotations

import re
from typing import Any

from app.services.chatbot import jsc

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

# The JS renders an empty value and an object joiner with an EM DASH, and the access note
# with one too. Written as an escape, not as the character: the repo forbids an em dash in
# anything WE write, and this is a literal from a node body that ships, and changing it
# would change what a customer is sent.
EM_DASH = "\u2014"


def timestamp(iso: Any) -> str | None:
    """`new Date(iso)` then local `getDate()`/`getMonth()`/... - components, verbatim.

    Every captured `last_updated_at` is a NAIVE ISO string, which `new Date` reads as local
    time and `getDate()` then reads straight back, so the rendered components equal the
    input's (measured on all 20 `output-structurer` captures). Implemented as a component
    read rather than through a timezone, which is also what makes it deterministic.
    """
    if not jsc.truthy(iso):
        return None
    text = jsc.js_string(iso)
    match = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2}))?", text
    )
    if not match:
        return None
    year, month, day = match.group(1), match.group(2), match.group(3)
    date = f"{day}/{month}/{year}"
    hour, minute, second = match.group(4), match.group(5), match.group(6)
    if hour is None:
        return date
    if (int(hour), int(minute), int(second)) == (0, 0, 0):
        return date  # midnight -> date only
    return f"{date} {hour}:{minute}:{second}"


def value(v: Any) -> str:
    if v is None or v == "":
        return EM_DASH
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, str):
        # DO-ASK-SIMPLIFY tester pass 2: a value's own '*' (a product code "*REPLACE")
        # would pair with the next label's bold marker and leave a stray '*' in WhatsApp.
        # It prints as the look-alike U+2217, which WhatsApp does not read as formatting.
        return ((timestamp(v) or v) if ISO_RE.match(v) else v).replace("*", "∗")
    if isinstance(v, (int, float)):
        return jsc.js_string(v)
    if isinstance(v, list):
        return ", ".join(value(x) for x in v)
    if isinstance(v, dict):
        parts = [
            (value(x) if isinstance(x, (dict, list)) else jsc.js_string(x))
            for x in v.values()
            if x is not None and x != ""
        ]
        return f" {EM_DASH} ".join(parts) if parts else EM_DASH
    return jsc.js_string(v)


def label_line(label: str, v: Any) -> str:
    """One `*Label:* value` field line."""
    return f"*{label}:* {value(v)}"
