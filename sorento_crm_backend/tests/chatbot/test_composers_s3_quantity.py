"""CHAT-COMPOSER S3a red tests: the bare "How many units do you need?" beside a full answer (Q1,
narrowed by the lead on 17 Oct) and the owner-ruled texts that must not move.

Q1 as ruled: under a multi-domain answer the bare question is dropped, and no stock task opened,
ONLY when the stock leg was carried from an earlier turn per the parser's verdict (an inventory
entity whose `current_message` is not true). A stock leg asked in the current message keeps the
question and the task. Gate off: today's text and task in both cases.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.turn import compose as turn_compose
from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_composers_stock_xd import _set_gate

BARE = "How many units do you need?"
PROMO = "I have attached the file(s) below.\n\n1. *Promotion:* CABANA"


class _Spec:
    def __init__(self, domain):
        self.domain = domain
        self.entities = [{"uuid": "x"}]
        self.filters = {}


class _Plan:
    def __init__(self, *domains):
        self.fetch = [_Spec(d) for d in domains]


def _multi(*, current, in_message=None, hint="inventory"):
    from app.services.chatbot.engine import _stock_ask_reply

    entity = ht.asked("SRTKS7547")
    entity["current_message"] = current
    state = ht.state(availability_only=True)
    out = _stock_ask_reply(
        turn_compose.Answer(text=f"{BARE}\n\n{PROMO}"),
        state,
        ht.envelopes(ht.row("SRTKS7547")),
        _Plan("inventory", "promotion"),
        verdict(
            domain_hint=hint,
            entities=[entity],
            **({} if in_message is None else {"domain_in_message": in_message}),
        ),
        turn_no=5,
    )
    opened = [t for t in state.focus.tasks if t.kind == "stock_qty" and t.opened_at_turn == 5]
    return out, opened


def test_carried_stock_leg_drops_the_question_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    out, _opened = _multi(current=False, in_message=False, hint="promotion")
    assert BARE not in out.text and "How many units" not in out.text
    assert out.text.startswith("I have attached the file(s) below."), out.text
    assert "1. *Promotion:* CABANA" in out.text


def test_carried_stock_leg_opens_no_task_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    _out, opened = _multi(current=False, in_message=False, hint="promotion")
    assert opened == []


def test_current_message_asks_stock_about_carried_subject_keeps_question_gate_on(monkeypatch):
    """"stock for it" after a promo answer: every entity is carried, but the parser says this
    message asks the inventory domain, so the question and the task stay."""
    _set_gate(monkeypatch, True)
    out, opened = _multi(current=False, in_message=True, hint="inventory")
    assert out.text == f"{BARE}\n\n{PROMO}"
    assert len(opened) == 1


def test_current_message_stock_leg_keeps_question_and_task_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    out, opened = _multi(current=True)
    assert out.text == f"{BARE}\n\n{PROMO}"
    assert len(opened) == 1


@pytest.mark.parametrize("current", [False, True])
def test_multi_domain_gate_off_golden(monkeypatch, current):
    _set_gate(monkeypatch, False)
    out, opened = _multi(current=current)
    assert out.text == f"{BARE}\n\n{PROMO}"
    assert len(opened) == 1


@pytest.mark.parametrize("gate", [False, True])
def test_stock_only_question_unchanged(monkeypatch, gate):
    """Single-domain stock turn: the named question and the owner-ruled `N. CODE - ` lines."""
    from app.services.chatbot.engine import _stock_ask_reply

    _set_gate(monkeypatch, gate)
    state = ht.state(availability_only=True)
    out = _stock_ask_reply(
        turn_compose.Answer(text=BARE),
        state,
        ht.envelopes(ht.row("ELP3754"), ht.row("SRTKT1631SS")),
        _Plan("inventory"),
        verdict(domain_hint="inventory", entities=[ht.asked("ELP3754"), ht.asked("SRTKT1631SS")]),
        turn_no=1,
    )
    # single product: the named question
    single_state = ht.state(availability_only=True)
    single = _stock_ask_reply(
        turn_compose.Answer(text=BARE),
        single_state,
        ht.envelopes(ht.row("ELP3754")),
        _Plan("inventory"),
        verdict(domain_hint="inventory", entities=[ht.asked("ELP3754")]),
        turn_no=1,
    )
    assert single.text == "How many units of ELP3754?"
    assert [t.kind for t in state.focus.tasks] == ["stock_qty"]
    assert out.text == "How many units for each?\n1. ELP3754 - \n2. SRTKT1631SS - "


def test_sibling_picker_unchanged_both_gate_states(monkeypatch):
    from tests.chatbot.test_rearch_r4_bridge_miss import _expected_miss_text
    from tests.chatbot.test_rearch_r8_recheck_round import _dym_incoming_sibling_scenario

    texts = []
    for gate in (False, True):
        _set_gate(monkeypatch, gate)
        parser, resolved, extra = _dym_incoming_sibling_scenario()
        payload = {"resolved": resolved, "gate": extra["gate"], "_exit_kind": "not_found"}
        text, _offer = _expected_miss_text(
            payload,
            parser=parser,
            resolved=resolved,
            gate=extra["gate"],
            services=extra["services"],
            build_result=extra["build_result"],
        )
        texts.append(text)
    assert texts[0] == texts[1] == (
        "No incoming stock (ETA) found for SRTWT7202. Related products:\n1. SRTWT7202-BL - has incoming"
        "\n2. SRTWT7202-GM - no incoming\nReply with a number to check its incoming, or reply 'yes' to"
        " escalate to purchasing team."
    )
