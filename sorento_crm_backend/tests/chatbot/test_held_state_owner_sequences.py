"""STUCK-QTY-LOOP crew design review P1 (3): the owner's picker sequence through the central
held-question rule (`turn/held.py`), on PICKER-ESCALATION's replay (#1467).

"delivery for yoo living" prints the customer picker; "1" answers it; no orders match, so the
reply offers escalation. The held rule must keep every step of that: the pick is an answer
(never a new ask), the offer follows it, and an unrelated next ask is answered as itself,
never as the offer.
"""
from __future__ import annotations

from app.services.chatbot.turn import held
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_picker_escalation_offer import YOO, _chat
from tests.chatbot.test_samantha_27sep_r5_do_list_carry import ORDERS_LIST, _order_ask, _pick
from tests.chatbot.test_samantha_27sep_r7_owner_replay import ESCALATE_WORDS


def _yoo_ask_with_window():
    """#1467's ask with a period stated: DO-ASK-SIMPLIFY (#1433) asks "Which period?" before
    a delivery list with none, which is not the step this sequence is about."""
    return _order_ask(
        entities=[{"raw": YOO, "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}],
        document=["DO"],
        status="delivered",
        date_mode="range",
        date_filter_start="2026-09-01",
        date_filter_end="2026-09-30",
    )


def test_picker_then_1_then_empty_offers_escalation_and_holds_it(session_factory, monkeypatch) -> None:
    chat, app = _chat(session_factory, monkeypatch, escalation_allowed=True)
    try:
        picker, calls, _ = chat.turn("delivery for yoo living last month", _yoo_ask_with_window())
        assert "YOO LIVING HOUSE" in picker and not [n for n, _ in calls if n == ORDERS_LIST], picker
        reply, calls, _ = chat.turn("1", _pick(1))
        assert [a for n, a in calls if n == ORDERS_LIST], (reply, calls)
        assert any(w in reply.casefold() for w in ESCALATE_WORDS), reply
        assert held.LOOP_REPLY not in reply

        other, _calls, _ = chat.turn("check stock SRT5674", _parser_output(
            intent_hint="check_stock", domain_hint="inventory",
            entities=[{"raw": "SRT5674", "hint": "product", "canonical_code": None,
                       "current_message": True, "confident": True}],
        ))
        assert other.strip() != reply.strip(), other
        assert "YOO LIVING HOUSE" not in other, other
    finally:
        app.dependency_overrides.clear()
