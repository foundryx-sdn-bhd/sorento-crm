"""Chat fix PR-A fix round 1 F3 (AC-A11, W12 "Do you have any bathtub?"), whole turn: a
class-only product ask with no product code is a described set, so the reply states how
many there are, not a page of ten looked-up codes.

The parse is a hand-built verdict (no live LLM call); the world is round 10's.
"""
from __future__ import annotations

import re

from tests.chatbot.test_attribute_asks_round3 import _entity
from tests.chatbot.test_attribute_asks_round3 import world as r3world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round4 import world as r4world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round7 import _code_ask
from tests.chatbot.test_attribute_asks_round7 import world as r7world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round10 import chat, embedding_says_trap, world  # noqa: F401 - fixtures used by name
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name

MESSAGE = "Do you have any bathtub?"


def test_a_class_only_ask_states_the_set_total(chat, world):
    from app.models.product_spec import ProductSpecifications

    db = world["db"]
    tubs = {
        p.product_code
        for p in world["every"]
        if ((db.query(ProductSpecifications).filter_by(product_id=p.id).one().values or {}).get("class") or {}).get("value")
        == "Bathtub"
    }
    assert tubs, "the world holds bathtubs"
    text = chat.say(MESSAGE, _code_ask(MESSAGE, _entity("bathtub", "category")))
    print(text)
    head = text.split("\n")[0]
    assert head.startswith("Here's what you want:"), text
    assert re.search(rf"\({len(tubs)}\b", head), text
