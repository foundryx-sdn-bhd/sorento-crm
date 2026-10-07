"""QUOTED-REPLY AC-QR-26, the pending half: a quoted answer that opens a NEW picker while a live
stock task is open replaces it. The picker is the turn's own `asked` question, so the live task C
is dropped like a fresh ask drops it. Synthetic ids; catalogue-style codes.
"""
from __future__ import annotations

import pytest

from tests.chatbot._avail_mode_console import _seed
from tests.chatbot._r9_engine_console import OWNER_FAMILY, EngineConsole, product, stock
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_quoted_reply_engine import C, E2_CODE, _focus_wire, _live_c, _open_task_labels, _store_turn, say_quoted

pytestmark = pytest.mark.usefixtures("stub_access")


def test_ac_qr_26_quoted_message_opening_a_new_picker_drops_the_live_task(session_factory, monkeypatch, stub_access):
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000701")
    c.codes.update(_seed(session_factory, [E2_CODE]))
    c.uuid_of = {code: pid for pid, code in c.codes.items()}
    quoted_text = f"I have {E2_CODE}. What would you like me to know?"
    _store_turn(c, reply_text=quoted_text, focus=_focus_wire(c, E2_CODE), turn_no=1, age_s=900)
    _live_c(c)
    assert _open_task_labels(c) == [C]

    say_quoted(c, "check stock srtwc286", stock(product("srtwc286")), quote=quoted_text)

    question = c.stored_question
    assert question is not None and question["kind"] == "product_pick", question
    assert [o["label"] for o in question["options"]] == OWNER_FAMILY
    assert _open_task_labels(c) == [], "the live task C is dropped like a fresh ask drops it"
