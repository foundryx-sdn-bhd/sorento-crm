"""CHAT-STATE S6 (red): an `unclear` held slot is asked once with real names (R15, UAC C10).

Same dry-run harness as the S4 seam tests (parser bypassed by `mock_reformulator_output`).
Policy default: `chat_state.unclear_slot_default()` is "ask" today; "assume" applies the slot.
"""
from __future__ import annotations

import json
import re
from typing import Any

from tests.chatbot.test_chat_state_seam import (  # noqa: F401 - fixtures used by name
    CUSTOMER_UUID,
    HELD_WINDOW,
    _emit,
    _held_customer_state,
    _mem_state,
    _op,
    seam,
    stub_access,
)

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _delivery(*changes: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return _emit(
        domain_hint="order", intent_hint="check_order", domain_in_message=True,
        state_changes=list(changes), **extra,
    )


def _text(out: Any) -> str:
    return (out.reply or {}).get("text") or ""


def _ask(seam):
    prev = _held_customer_state(window=HELD_WINDOW)
    return seam.say("delivery?", _delivery(_op("keep"), _op("unclear", "period")), previous=prev)


def _blob(seam) -> str:
    return json.dumps(seam.turn_calls[-1], default=str)


def _period_words(reply: str) -> bool:
    return any(t in reply for t in ("1 Oct", "01/10", "2026-10-01", "1/10", "Oct 1"))


def _no_slot_question(seam, note: str = "") -> None:
    """No period/customer slot question is open. The stub tool finds no rows, so the ordinary
    escalation offer after a miss may be stored; that is not the slot question under test."""
    question = seam.state["open_question"] or {}
    labels = [str(o.get("label") or "") for o in question.get("options") or []]
    assert not any(_period_words(label) or "Customer X" in label or "all" in label.lower() for label in labels), (
        note, question,
    )


def test_the_policy_default_is_ask_in_one_named_place():
    from app.services.chatbot.turn import chat_state

    assert chat_state.unclear_slot_default() == "ask"


def test_unclear_customer_is_settled_by_the_held_entity_type_with_no_question(seam):
    """The held entity is a `customer` kind: the ask is a delivery ask for it (owner 16 Oct ~23:00)."""
    prev = _held_customer_state(window=HELD_WINDOW)
    out = seam.say("delivery?", _delivery(_op("keep"), _op("unclear", "customer")), previous=prev)
    _no_slot_question(seam, _text(out))
    assert CUSTOMER_UUID in _blob(seam), _text(out)
    assert not UUID_RE.search(_text(out))


def test_unclear_period_asks_once_naming_the_held_period_and_another_with_no_ids(seam):
    out = _ask(seam)
    reply = _text(out)
    assert _period_words(reply), reply
    assert not UUID_RE.search(reply) and CUSTOMER_UUID not in reply, reply
    assert "2026-10-07" not in _blob(seam), "nothing is fetched while asking"
    question = seam.state["open_question"]
    assert question and len(question["options"]) == 2, question
    assert _period_words(question["options"][0]["label"])


def test_the_answer_fills_only_the_period_slot_and_the_ask_runs_with_that_period(seam):
    _ask(seam)
    out = seam.say(
        "1",
        _delivery(
            _op("answer"),
            reference_positions=[1],
            open_question_answer={"mode": "pick", "picked": [1], "items": [], "qty_for_all": None},
        ),
    )
    assert "2026-10-01" in _blob(seam), (_blob(seam)[:400], _text(out))
    assert [c.get("canonical_code") for c in seam.state["focus"]["customers"]] == ["C0001"]
    _no_slot_question(seam, "the slot question is closed once answered")


def test_with_the_default_set_to_assume_no_question_is_asked_and_the_held_period_applies(seam, monkeypatch):
    from app.services.chatbot.turn import chat_state

    monkeypatch.setattr(chat_state, "unclear_slot_default", lambda: "assume")
    out = _ask(seam)
    _no_slot_question(seam)
    assert "2026-10-01" in _blob(seam), _text(out)
    assert not UUID_RE.search(_text(out))


def test_a_second_unclear_right_after_the_bot_asked_offers_the_hand_over_and_never_guesses(seam):
    _ask(seam)
    out = seam.say("delivery??", _delivery(_op("keep"), _op("unclear", "period")))
    reply = _text(out).lower()
    assert any(w in reply for w in ("team", "staff", "colleague", "human", "someone", "person")), reply
    assert "2026-10-01" not in _blob(seam), "no guess"
    assert not UUID_RE.search(reply)
    assert _mem_state(seam, out).get("changes")
