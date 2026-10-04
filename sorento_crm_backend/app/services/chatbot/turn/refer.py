"""CUSTOMER-ASKS-REFER-ONLY (owner ruling 1 Oct 2026): the ONE way a backend composer
prints "Please refer to your salesman." (`task.py::REFER_TO_SALESMAN`), and the turn-level
mark that says it did.

Customer asks logs every reply that refers the customer to their salesman and only those.
The ask writer (`engine._record_customer_asks`) reads `Mark.referred` rather than the reply
text, so every composer prints the line through `sentence()` / `after()` here, never the
constant itself (`tests/chatbot/test_customer_asks_refer_only.py` holds the guard).

The mark lives on a context variable for the length of one turn (`tracking()`, opened by
`engine.run_turn` and `engine.complete_turn`; a `complete_turn` called from inside
`run_turn` shares the outer turn's mark). The chatbot runs a turn on one thread, start to
end, so the composer and the writer see the same mark. Outside a turn the helpers still
print the line and mark nothing.

The MCP presenter prints the stock ask's own lines in another process; it stamps
`refers_to_salesman` on each `stock_availability` entry instead (`presenters.py`).

Pure: no I/O.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from app.services.chatbot.turn.task import REFER_TO_SALESMAN

__all__ = ["Mark", "SALESMAN_TEAM", "after", "consume", "sentence", "tracking"]

#: The business lane's "team" a barred contact is offered instead of a hand-off
#: (`lanes/business/answer.py::what_you_want_reply` compares against it and prints the line
#: through `after`). The same words, so a caller that passes the sentence still matches.
SALESMAN_TEAM = REFER_TO_SALESMAN


@dataclass
class Mark:
    referred: bool = False


_CURRENT: ContextVar[Mark | None] = ContextVar("chatbot_refer_mark", default=None)


@contextmanager
def tracking() -> Iterator[Mark]:
    """The turn's mark: a fresh one, or the enclosing turn's when one is already open."""
    current = _CURRENT.get()
    if current is not None:
        yield current
        return
    mark = Mark()
    token = _CURRENT.set(mark)
    try:
        yield mark
    finally:
        _CURRENT.reset(token)


def _mark() -> None:
    current = _CURRENT.get()
    if current is not None:
        current.referred = True


def sentence() -> str:
    """The refer line on its own, and the turn marked."""
    _mark()
    return REFER_TO_SALESMAN


def after(text: str, *, sep: str = "\n\n") -> str:
    """`text`, then the refer line (`sep` between them), and the turn marked. An empty
    `text` is the line alone."""
    _mark()
    return f"{text}{sep}{REFER_TO_SALESMAN}" if text else REFER_TO_SALESMAN


def consume() -> bool:
    """Whether this turn printed the refer line, clearing the mark: the ask writer reads it
    once, so a second tail in the same turn cannot log the reply twice."""
    current = _CURRENT.get()
    if current is None:
        return False
    referred, current.referred = current.referred, False
    return referred
