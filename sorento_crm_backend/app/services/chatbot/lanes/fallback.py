"""Graceful fallback replies (PLAN-chatbot-memory-26sep.md section 7, slice S4).

A reply to a message the bot cannot answer from data is

    reply = ack            one human sentence, the clarifier's (the ONLY LLM-written part)
          + lead + offer   deterministic: a history list or a noted fact, then the concrete
                           next thing: a numbered choice, a person

Everything here is pure: the engine reads the database (`engine._fallback_context`), the
clarifier answers, and `compose` puts the parts together from the canned copy.

**The guard (AC-MEM081).** Facts reach the dealer only through the lead line and the
composers, which read data. An ack that names a figure, a product-code-shaped token, a
price or a date its own input never had is replaced by the canned ack for the language,
so a model that "helpfully" invents "25 units at RM12.50" never reaches WhatsApp.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.chatbot import jsc

#: The ack's length cap (plan 7.2): longer than this is a reply, not an acknowledgement.
ACK_MAX_WORDS = 25

#: The clarifier's memory slice cap, est. tokens (plan S4: "the L5 + L4 slice under 400").
CLARIFIER_MEMORY_TOKENS = 400

#: How many conversations the history reply lists (AC-MEM082: "up to 5").
HISTORY_ITEMS = 5

LANGUAGES = ("en", "ms", "zh")

_WORD_SPLIT = re.compile(r"\s+")
_EDGE_PUNCT = "\"'.,!?;:()[]{}<>*_~`"
_MONTHS = frozenset(
    "jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july "
    "august september october november december".split()
)
_WEEKDAYS = frozenset(
    "mon tue tues wed thu thur thurs fri sat sun monday tuesday wednesday thursday friday saturday "
    "sunday".split()
)
_CURRENCY = ("rm", "myr", "usd", "sgd", "rmb", "$", "¥", "￥", "元")
_CJK_DIGITS = frozenset("〇一二三四五六七八九十百千万")


def _is_suspicious(token: str) -> bool:
    """A token that states a fact: a digit anywhere (a quantity, a price, a date, most
    codes), a currency marker, a month or weekday name, or a code shape (letters joined by
    a hyphen, or an all-caps word of 5+ letters)."""
    bare = token.strip(_EDGE_PUNCT)
    if not bare:
        return False
    low = bare.lower()
    if any(ch.isdigit() for ch in bare) or any(ch in _CJK_DIGITS for ch in bare):
        return True
    if any(low.startswith(c) for c in _CURRENCY):
        return True
    if low in _MONTHS or low in _WEEKDAYS:
        return True
    if "-" in bare and any(ch.isalpha() for ch in bare) and bare.upper() == bare:
        return True
    return len(bare) >= 5 and bare.isalpha() and bare.isascii() and bare.upper() == bare


def ack_is_safe(ack: str, own_input: str) -> bool:
    """True when every fact-shaped token of the ack is also in the clarifier's own
    input (the message, the dealer's name, the memory slice), and the ack is short."""
    words = [w for w in _WORD_SPLIT.split(ack.strip()) if w]
    if not words or len(words) > ACK_MAX_WORDS:
        return False
    haystack = own_input.lower()
    for word in words:
        if _is_suspicious(word) and word.strip(_EDGE_PUNCT).lower() not in haystack:
            return False
    return True


@dataclass(frozen=True)
class ClarifierAnswer:
    """What the clarifier said, read leniently. `shape` is `ack` for the S4 contract
    (`{"ack", "language"}`) and `response` for a prompt version that still answers in
    the older `{"response"}` shape: that one is the whole reply, as it always was."""

    text: str
    language: str | None
    shape: str


def read_clarifier(parsed: Any) -> ClarifierAnswer | None:
    """`None` when there is nothing to say (the caller raises `ClarifierAnswerEmpty`)."""
    if isinstance(parsed, dict):
        language = parsed.get("language")
        language = language if language in LANGUAGES else None
        ack = parsed.get("ack")
        if jsc.truthy(ack) and str(ack).strip():
            return ClarifierAnswer(text=jsc.js_string(ack).strip(), language=language, shape="ack")
        response = parsed.get("response")
        if jsc.truthy(response) and str(response).strip():
            return ClarifierAnswer(text=jsc.js_string(response), language=language, shape="response")
        return None
    text = jsc.js_string(parsed)
    if not text.strip():
        return None
    return ClarifierAnswer(text=text, language=None, shape="response")


def pick_language(*candidates: str | None) -> str:
    """The first known language of: the contact's saved one, one stated this turn, the
    clarifier's reading; else English (AC-MEM089)."""
    for value in candidates:
        if value in LANGUAGES:
            return str(value)
    return "en"


@dataclass
class FallbackContext:
    """Everything `compose` needs, read by the engine while its session is open.

    `kind`: `history` (a question about the dealer's own past), `unknown` (a message the
    bot cannot place) or `small_talk` (greetings, thanks, off-topic). `level` is the
    contact's effective memory level; every memory field is already empty when the
    level does not grant its layer (plan 6.0)."""

    kind: str
    level: str
    history: list[str] = field(default_factory=list)
    last_time: str | None = None
    usual_products: list[str] = field(default_factory=list)
    usual_site: str | None = None
    customer: str | None = None
    team: str | None = None
    noted: list[dict[str, Any]] = field(default_factory=list)
    saved_language: str | None = None
    first_name: str | None = None
    memory_slice: str = ""
    #: The turn's resolved canned copy (`chatbot/copy.CannedCopy`), read with the rest.
    copy: Any = None


def clarifier_tail(fb: FallbackContext) -> str:
    """Appended to the clarifier's user message: the memory slice its ack may lean on,
    the dealer's stored first name, and the S4 output contract. The clarifier's system
    prompt is untouched; this is the turn's own instruction."""
    lines = []
    if fb.first_name:
        lines.append(f"first_name: {fb.first_name}")
    if fb.memory_slice:
        lines.append("memory:")
        lines.append(fb.memory_slice)
    lines.append(
        'reply_format: return ONLY {"ack": "...", "language": "en|ms|zh"}. "ack" is ONE short '
        f"human sentence (at most {ACK_MAX_WORDS} words) acknowledging the message, in the "
        "dealer's language, and may use first_name. No numbers, product codes, prices or "
        "dates, and no question: the next step is added after it. \"language\" is the "
        "language the dealer wrote in."
    )
    return "\n".join(lines)


def _join(parts: list[str]) -> str:
    kept = [p.strip() for p in parts if p and p.strip()]
    if any("\n" in p for p in kept):
        return "\n\n".join(kept)
    return " ".join(kept)


def _noted_lines(fb: FallbackContext, copy: Any, lang: str) -> list[str]:
    out: list[str] = []
    for statement in fb.noted:
        key, value = statement.get("key"), statement.get("value")
        if key == "role" and isinstance(value, str) and value.strip():
            if fb.customer:
                out.append(copy.render_in("fallback_noted_role", lang, role=value.strip(), customer=fb.customer))
            else:
                out.append(copy.render_in("fallback_noted_role_no_customer", lang, role=value.strip()))
        elif key == "language" and value in LANGUAGES:
            # Confirmed in the language the dealer asked for, whatever the ack's.
            out.append(copy.render_in("fallback_noted_language", str(value)))
        else:
            out.append(copy.render_in("fallback_noted", lang))
    # One confirmation per kind is plenty; keep the first of each.
    seen: set[str] = set()
    return [line for line in out if not (line in seen or seen.add(line))]


def lead_and_offer(fb: FallbackContext, copy: Any, lang: str) -> tuple[str, str]:
    """The deterministic halves of the reply (lead line, offer). Nothing here reads a figure."""
    if fb.kind == "history":
        if not fb.history:
            return copy.render_in("history_nothing", lang), copy.render_in("fallback_offer", lang)
        numbered = "\n".join(f"{i}. {item}" for i, item in enumerate(fb.history[:HISTORY_ITEMS], start=1))
        return f"{copy.render_in('history_lead', lang)}\n{numbered}", copy.render_in("history_offer", lang)

    noted = _noted_lines(fb, copy, lang)
    if noted:
        return " ".join(noted), copy.render_in("fallback_offer", lang)

    if fb.kind == "unknown" and fb.customer and fb.team:
        return "", copy.render_in("fallback_offer_customer", lang, customer=fb.customer, team=fb.team)

    if fb.usual_products:
        products = " or ".join(fb.usual_products[:2]) if lang == "en" else ", ".join(fb.usual_products[:2])
        if fb.usual_site:
            offer = copy.render_in("fallback_offer_usual_site", lang, site=fb.usual_site, products=products)
        else:
            offer = copy.render_in("fallback_offer_usual", lang, products=products)
    else:
        offer = copy.render_in("fallback_offer", lang)
    return "", offer


def compose(ack: str, fb: FallbackContext, copy: Any, lang: str) -> str:
    lead, offer = lead_and_offer(fb, copy, lang)
    return _join([ack, lead, offer])
