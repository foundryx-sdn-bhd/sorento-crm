"""PV2 redesign R4a fix 2, C1: a thank-you mid-session closes without a recap. Engine level (the real
`engine.run_turn` on Postgres, stubbed parser verdict and clarifier), using the S4 fallback replay harness
(`tests/chatbot/test_memory_s4_fallback_replay.py`). The reply is graded by what is ABSENT, never exact wording.

The decision lives in `engine._fallback_context` / `fallback.compose` (engine.py ~8746-8757, lanes/fallback.py ~206-216).
"""
from __future__ import annotations

from tests.chatbot.test_engine import stub_access  # noqa: F401 - fixture
from tests.chatbot.test_memory_s4_fallback_replay import (  # noqa: F401 - fixtures and helpers
    _run_turn,
    _seed_contact,
    _seed_frames,
    lane,
    sent_text,
)

GIVEN = {"first_name": "Mei", "frames": [
    {"days_ago": 3, "domain": "inventory",
     "summary": "Asked about stock for SRTWB1455 and got an answer."}]}
STOCK_ASK = {"domain_hint": "inventory", "intent_hint": "check_stock", "message_type": "business",
             "entities": [{"raw": "SRTWB1455", "hint": "product", "canonical_code": None,
                           "current_message": True, "confident": True}]}


def _thanks(session_factory, stub_access, lane, *, earlier_answer: bool, message: str):
    pk = _seed_contact(session_factory, GIVEN, level="full")
    _seed_frames(session_factory, GIVEN["frames"], is_test=False)
    n = 0
    if earlier_answer:
        _run_turn(session_factory, stub_access, message="stock SRTWB1455", verdict_overrides=STOCK_ASK, n=n, console=False)
        n += 1
    lane.clarifier_answer = {"ack": "Anytime.", "language": "en"}
    result, _ = _run_turn(session_factory, stub_access, message=message,
                          verdict_overrides={"message_type": "casual"}, n=20, console=False)
    return sent_text(result)


def test_c1_a_thank_you_after_an_answer_in_this_session_has_no_recap_and_no_rerun_offer(session_factory, stub_access, lane):
    """RED: HEAD appends "Last time: ..." and the re-run offer to every small_talk reply that has history."""
    sent = _thanks(session_factory, stub_access, lane, earlier_answer=True, message="cheers, that helps a lot")
    assert sent.strip(), "some visible close"
    assert "Last time" not in sent, sent
    assert "again" not in sent.lower() and "something new" not in sent.lower(), sent  # the re-run offer


def test_c1_guard_a_greeting_opening_a_session_keeps_its_recap(session_factory, stub_access, lane):
    """GUARD: nothing earlier in the session; whatever the greeting does today stays (the recap of the past
    conversations)."""
    sent = _thanks(session_factory, stub_access, lane, earlier_answer=False, message="morning, anyone there")
    assert "Last time" in sent, sent
