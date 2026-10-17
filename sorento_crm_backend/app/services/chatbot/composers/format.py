"""Value and timestamp printers shared by the reply composers.

`timestamp` / `value` / `EM_DASH` / `ISO_RE` moved byte for byte out of
`lanes/business/fetch.py`, which keeps its old names as aliases.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.chatbot import jsc

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def is_uuid(v: Any) -> bool:
    """A UUID-shaped token: an id, never a name a customer reads."""
    return isinstance(v, str) and bool(UUID_RE.fullmatch(v.strip()))


ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

# The JS renders an empty value and an object joiner with an EM DASH, and the access note
# with one too. Written as an escape, not as the character: the repo forbids an em dash in
# anything WE write, and this is a literal from a node body that ships, and changing it
# would change what a customer is sent.
EM_DASH = "\u2014"

# A whole-string date, as a SQL `Date` column serialises. Anchored at both ends: text that
# merely starts or ends with a date ("2026-09-27 to 2026-10-01", "SRT-2026-09-27") is not one.
DATE_ONLY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_PREFIX_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?")
_DMY_RE = re.compile(r"\d{2}/\d{2}/\d{4}")


def date(v: Any) -> str | None:
    """`dd/mm/yyyy` from `YYYY-MM-DD` with or without a time part (components read verbatim,
    no timezone shift); an already `dd/mm/yyyy` string passes; anything else is None."""
    if not isinstance(v, str):
        return None
    if _DMY_RE.fullmatch(v):
        return v
    match = _DATE_PREFIX_RE.fullmatch(v)
    return f"{match.group(3)}/{match.group(2)}/{match.group(1)}" if match else None


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
        if DATE_ONLY_RE.fullmatch(v):
            from app.services.chatbot import composers

            if composers.enabled():
                return date(v) or v
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


def label(f: Any) -> str:
    """The printed label of a field, by its KEY where two tools name one thing differently."""
    key = f.get("key") if isinstance(f, dict) else None
    if key == "shipping_container_number":
        return "Container"
    if key == "estimated_arrival_date":
        return "ETA"
    return jsc.js_string(jsc.get(f, "label", jsc.UNDEFINED))


def missing_line(what: str, product: str, *, fill: Any = None) -> str:
    """The one "nothing on record" sentence: `No Product Photos on record for X.`"""
    from app.services.chatbot.composers.misses import PHRASE_ON_RECORD

    key = f"No {{what}}{PHRASE_ON_RECORD}{{product}}."
    if fill is not None:
        return fill(key, what=what, product=product)
    return key.format(what=what, product=product)


def money(amount: Any, currency: str = "MYR") -> str:
    """`MYR 1,234.00`: the one money shape (R13)."""
    return f"{currency} {Decimal(str(amount)):,.2f}"


#: The keyed money fields (`_line_money`) and the unkeyed ones (`_money`) the presenter prints
#: as `CUR 1234.00`; `money_field` is the inverse of that one printer.
MONEY_KEYS = frozenset({"unit_cost", "discount_per_unit", "unit_cost_after_discount"})
MONEY_LABELS = frozenset({"List Price", "Selling Price"})
_MONEY_VALUE_RE = re.compile(r"([A-Z]{2,3}) (-?\d+\.\d{2})")


def money_field(f: Any) -> Any:
    """The field's value, with a presenter money value reprinted `MYR 1,234.00`; anything else as is."""
    value = f.get("value") if isinstance(f, dict) else None
    if not (isinstance(f, dict) and (f.get("key") in MONEY_KEYS or f.get("label") in MONEY_LABELS)):
        return value
    match = _MONEY_VALUE_RE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return value
    try:
        return money(match.group(2), match.group(1))
    except InvalidOperation:
        return value


def present(v: Any) -> bool:
    """False for the empty values and the presenter's `Not defined` marker."""
    if v is None:
        return False
    text = str(v).strip()
    return bool(text) and text != "Not defined"


_TITLE_WORD_RE = re.compile(r"[A-Z][a-z]+")


def noun(label: str) -> str:
    """A label as a noun in a sentence: each Title-case word lower-cased, other words kept."""
    return " ".join(w.lower() if _TITLE_WORD_RE.fullmatch(w) else w for w in label.split(" "))
