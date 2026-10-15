"""Chat fix PR-A round 2 (W03 regression, live walkthrough): "What is the stock for Andamal
Basin?" names a word nothing in the catalogue holds ("andamal") beside a class word. The reply
keeps the say-back ("Couldn't find: "andamal"") for staff and for a dealer, and a whole-class
stock set never turns into a per-item quantity ask.

The parses are the two the live parser gave: one product token "Andamal Basin" (v94), and one
category token "Andamal Basin" whose canonical_code is the class name "Wash Basin" (v104).
No live LLM call. The world is round 10's, whose wash basins have stock.
"""
from __future__ import annotations

import pytest

from tests.chatbot.test_attribute_asks_round3 import _entity
from tests.chatbot.test_attribute_asks_round3 import world as r3world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round4 import world as r4world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round7 import _code_ask
from tests.chatbot.test_attribute_asks_round7 import world as r7world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round10 import embedding_says_trap, world  # noqa: F401 - fixtures used by name
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name

MESSAGE = "What is the stock for Andamal Basin?"


def _as_product():
    return _entity("Andamal Basin", "product")


def _as_category_named_by_its_class():
    return {**_entity("Andamal Basin", "category"), "canonical_code": "Wash Basin"}


@pytest.mark.parametrize("token", [_as_product, _as_category_named_by_its_class], ids=["v94-product", "v104-category-canonical"])
@pytest.mark.parametrize("mode", ["compact", "detailed"])
def test_an_unknown_word_beside_a_class_word_is_said_back_on_a_stock_ask(session_factory, monkeypatch, stub_parser, stub_access, world, embedding_says_trap, mode, token):
    from tests.chatbot.test_attribute_asks_round8 import _Loo

    chat = _Loo(session_factory, monkeypatch, stub_parser, stub_access, world, mode)
    text = chat.say(MESSAGE, _code_ask(MESSAGE, token(), intent_hint="check_stock", domain_hint="inventory"))
    print(text)
    assert 'Couldn\'t find: "andamal"' in text, text
    assert "How many units" not in text, text
