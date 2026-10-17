"""Miss pieces: the one "Couldn't find" sentence, the customer word for a domain key, and the
rule for when the bare quantity question rides with an answer."""
from __future__ import annotations

from typing import Callable

from app.services.chatbot.composers import Reply

#: Domain keys that are table names, and the word a customer reads instead. Every other key
#: prints as its words ("delivery_orders" -> "delivery orders").
MISS_WORD = {"inventory": "stock", "master_products": "product", "spo_allocation": "SPO line"}

#: The presenter's bare quantity question, printed above a stock answer that has none of its own.
BARE_QUANTITY_QUESTION = "How many units do you need?"


def not_found_text(names: list[str], *, join: Callable[[list[str]], str] | None = None) -> str:
    joined = join(names) if join else ", ".join(names)
    return f"Couldn't find {joined}."


def not_found(names: list[str], *, join: Callable[[list[str]], str] | None = None) -> Reply:
    return Reply(not_found_text(names, join=join))


def domain_word(key: str) -> str:
    return MISS_WORD.get(key) or key.replace("_", " ")


def quantity_question_rides(domains: list[str]) -> bool:
    """The bare quantity question belongs to a stock-only reply."""
    return list(domains) == ["inventory"]
