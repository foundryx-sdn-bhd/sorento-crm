"""Miss pieces: the one "Couldn't find" sentence, the customer word for a domain key, and the
rule for when the bare quantity question rides with an answer."""
from __future__ import annotations

from typing import Callable, Iterable

from app.services.chatbot.composers import Reply

#: Domain keys that are table names, and the word a customer reads instead. Every other key
#: prints as its words ("delivery_orders" -> "delivery orders").
MISS_WORD = {"inventory": "stock", "master_products": "product", "spo_allocation": "SPO line"}

#: The presenter's bare quantity question, printed above a stock answer that has none of its own.
BARE_QUANTITY_QUESTION = "How many units do you need?"


def not_found_text(names: list[str], *, join: Callable[[list[str]], str] | None = None) -> str:
    from app.services.chatbot.composers.format import is_uuid

    names = [n for n in names if not is_uuid(n)]  # an id is not a name
    joined = join(names) if join else ", ".join(names)
    return f"Couldn't find {joined}."


def not_found(names: list[str], *, join: Callable[[list[str]], str] | None = None) -> Reply:
    return Reply(not_found_text(names, join=join))


def domain_word(key: str) -> str:
    return MISS_WORD.get(key) or key.replace("_", " ")


def quantity_question_rides(domains: list[str]) -> bool:
    """The bare quantity question belongs to a stock-only reply."""
    return list(domains) == ["inventory"]


def bare_question_forms() -> frozenset[str]:
    """The bare quantity question as it can be printed: English and each catalogued translation."""
    from app.services.chatbot.label_catalog import LABELS

    entry = LABELS.get(BARE_QUANTITY_QUESTION) or {}
    return frozenset({BARE_QUANTITY_QUESTION, *[v for v in entry.values() if isinstance(v, str)]})


#: The phrases the absence-stating writers build their sentences from: `what_you_want_reply` and
#: `build_breakdown_msg` ("But no X matched these."), `build_suggest_offer` ("No incoming stock
#: (ETA) found for X."), and the gate-on "No <type> on record for <code>." lines. `states_absence`
#: reads exactly these, so a reworded writer cannot drift from its reader.
PHRASE_MATCHED = "matched these"
PHRASE_FOUND_FOR = " found for "
PHRASE_ON_RECORD = " on record for "
ABSENCE_PHRASES = (PHRASE_MATCHED, PHRASE_FOUND_FOR, PHRASE_ON_RECORD)


def states_absence(text: str, codes: "Iterable[str]") -> bool:
    """Does `text` itself say that every one of `codes` has nothing (not merely name it)?"""
    wanted = [c.lower() for c in codes if c]
    if not wanted:
        return False
    lines = [ln.lower() for ln in text.splitlines()]
    states = [ln for ln in lines if any(p in ln for p in ABSENCE_PHRASES)]
    body = "\n".join(lines)
    return bool(states) and all(c in body for c in wanted)
