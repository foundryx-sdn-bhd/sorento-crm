"""CHAT-LANGUAGE: per-message reply language (behaviour card, "How the language is chosen").

Deterministic word lists, no model. `detect` looks at the message after the codes and customer
names it was given are removed; `choose` is `pick_language`'s order with the message first.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from app.services.chatbot.label_catalog import LANGUAGES

_MALAY = frozenset(
    "ada tak berapa boleh nak saya stok barang bila sampai sudah belum ini itu untuk dengan "
    "mana harga lagi tolong ya bukan satu dua tiga kat yang apa dah ni tu betul mahu perlu semua".split()
)
_ENGLISH = frozenset(
    "do does did have has how many what when where which who is are can could will would please "
    "stock check any the an in on for of with still left there you your my need want got "
    "available incoming arriving".split()
)
_WORD = re.compile(r"[^\W\d_]+")


def detect(message: str, *, strip: Iterable[str] = ()) -> str | None:
    """`zh` on any CJK ideograph, else the language with more marker words; a tie or no marker
    word decides nothing (`None`)."""
    text = message or ""
    for phrase in strip:
        if phrase:
            text = re.sub(
                r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", " ", text, flags=re.IGNORECASE
            )
    if any("一" <= ch <= "鿿" for ch in text):
        return "zh"
    words = _WORD.findall(text.lower())
    ms = sum(1 for w in words if w in _MALAY)
    en = sum(1 for w in words if w in _ENGLISH)
    if ms > en:
        return "ms"
    if en > ms:
        return "en"
    return None


def choose(detected: str | None, conversation: str | None, saved: str | None) -> str:
    for candidate in (detected, conversation, saved):
        if candidate in LANGUAGES:
            return candidate
    return "en"


def for_turn(
    message: str,
    session_vars: dict[str, Any],
    saved: str | None,
    *,
    strip: Iterable[str] = (),
) -> str:
    """This turn's reply language, also written to `session_vars["reply_language"]` to carry."""
    lang = choose(detect(message, strip=strip), session_vars.get("reply_language"), saved)
    session_vars["reply_language"] = lang
    return lang
