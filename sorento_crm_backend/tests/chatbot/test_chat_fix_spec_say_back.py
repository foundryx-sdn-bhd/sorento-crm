"""Chat fix PR-A, AC-A6, whole turn: a spec word the registry cannot bind is said back on a
non-promotion ask, in the one reply structure's "Couldn't find" line, never dropped so the
ask answers unfiltered.

The parse is a hand-built verdict (no live LLM call); the world is round 10's (white water
closets among others). Grounding keeps the unknown word (`head/grounding.ground`), the
resolver hands it back as an unknown value, and the answer says it as typed.
"""
from __future__ import annotations

import pytest

from tests.chatbot.test_attribute_asks_round3 import _ask, _entity
from tests.chatbot.test_attribute_asks_round3 import world as r3world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round4 import world as r4world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round7 import _code_ask
from tests.chatbot.test_attribute_asks_round7 import world as r7world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round10 import chat, embedding_says_trap, world  # noqa: F401 - fixtures used by name
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name

MESSAGE = "any zztwonky water closet?"


def _unbound(raw: str) -> dict:
    """The parser's specification entity for a word no registry key holds."""
    return {**_entity(raw, "specification"), "spec_key": None, "spec_value": None}


@pytest.mark.parametrize(
    "verdict",
    [
        pytest.param(lambda: _code_ask(MESSAGE, _entity("water closet", "category"), _unbound("zztwonky")), id="product-ask"),
        pytest.param(lambda: _ask("stock", "water closet", MESSAGE, extra=[_unbound("zztwonky")]), id="stock-ask"),
    ],
)
def test_an_unbindable_spec_word_is_said_back_as_couldnt_find(chat, world, verdict):
    text = chat.say(MESSAGE, verdict())
    print(text)
    assert 'Couldn\'t find: "zztwonky".' in text, text
