"""QUOTED-REPLY round 7, AC-QR-26: a quoted answer's follow-up question replaces the live picker.

Owner hand test (7 Oct 2026): with a CKS105 picker live, quoting the older SRTWC286 picker and
picking 4 asked "How many units of SRTWC286-SH-NEW?", but the bare "10" after it hit the retained
CKS105 picker. `turn/tail.py::carry_live_state` restores the live pending whenever the turn
`asked` nothing of its own, and a quantity ask is a TASK question, so the picker survived.

Red-first: tests 1 and 3 fail today (picker A restored); test 2 is a guard and passes today.
Every id and phone is synthetic; the codes are catalogue-style codes.
"""
from __future__ import annotations

import pytest

from tests.chatbot._avail_mode_console import _seed
from tests.chatbot._r9_engine_console import OWNER_FAMILY, EngineConsole, answer, product, reply, stock
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_quoted_reply_engine import (
    A,
    B,
    C,
    E2_CODE,
    _answered,
    _focus_wire,
    _live_c,
    _open_task_labels,
    _store_asked,
    _store_turn,
    ask_text,
    say_quoted,
)

pytestmark = pytest.mark.usefixtures("stub_access")

CKS_FAMILY = [f"CKS105-Z{i}" for i in range(6)]
B_OPTION_4 = OWNER_FAMILY[3]  # SRTWC286-SH-NEW


def _console(session_factory, monkeypatch, stub_access, phone: str) -> EngineConsole:
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone=phone)
    c.codes.update(_seed(session_factory, CKS_FAMILY + ["SRTBF11841"]))
    c.uuid_of = {code: pid for pid, code in c.codes.items()}
    return c


def _open_pick(c: EngineConsole) -> dict:
    q = c.stored_question
    assert q is not None and q["kind"] == "product_pick", q
    return q


def _pick_verdict(position: int) -> dict:
    return reply(open_question_answer=answer("pick", picked=[position]))


def test_ac_qr_26_quoted_pick_followup_drops_the_live_picker(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000601")
    # Picker B (older, stored by the real engine with its `state` event), then picker A live.
    b_text = c.say("check stock srtwc286", stock(product("srtwc286")))
    b_q = _open_pick(c)
    assert [o["label"] for o in b_q["options"]] == OWNER_FAMILY
    c.say("check stock cks105", stock(product("cks105")))
    a_q = _open_pick(c)
    assert [o["label"] for o in a_q["options"]] == CKS_FAMILY
    assert a_q["payload"]["stock_pick"] is True

    out = say_quoted(c, "4", _pick_verdict(4), quote=b_text)
    assert out == f"How many units of {B_OPTION_4}?", out
    assert c.stored_question is None, f"picker A must not be restored: {c.stored_question}"
    assert _open_task_labels(c) == [B_OPTION_4]

    out2 = c.say("10", reply(demand_qty=10, open_question_answer=answer("fill", items=[(1, B_OPTION_4, 10)])))
    assert f"{B_OPTION_4} x 10:" in out2, out2
    assert "CKS105" not in out2
    assert "which one" not in out2.lower()
    assert "stock_pick_takes_quantity" not in (c.last_trace.rules_fired or [])


def test_ac_qr_26_quoted_fill_opening_nothing_keeps_the_live_task(session_factory, monkeypatch, stub_access):
    """Guard (AC-QR-8): a plain fill of an older ask opens nothing, so the live task C stays."""
    c = _console(session_factory, monkeypatch, stub_access, "+60900000602")
    _store_asked(c, A, turn_no=1, age_s=900)
    _live_c(c)
    out = say_quoted(
        c, "1 unit", reply(demand_qty=1, open_question_answer=answer("fill", items=[(1, A, 1)])), quote=ask_text(A)
    )
    assert out == _answered(A, 1)
    assert _open_task_labels(c) == [C]


def test_ac_qr_26_quoted_nonquestion_opening_a_task_drops_the_live_picker(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000603")
    e2_text = f"I have {E2_CODE}. What would you like me to know?"
    _store_turn(c, reply_text=e2_text, focus=_focus_wire(c, E2_CODE), turn_no=1, age_s=900)
    c.say("check stock cks105", stock(product("cks105")))
    assert _open_pick(c)["payload"]["stock_pick"] is True

    out = say_quoted(
        c,
        "have stock?",
        reply(entity_op="reuse", domain_hint="inventory", intent_hint="check_stock"),
        quote=e2_text,
    )
    assert out == f"How many units of {E2_CODE}?", out
    assert _open_task_labels(c) == [E2_CODE]
    assert c.stored_question is None, f"picker A must be dropped: {c.stored_question}"
