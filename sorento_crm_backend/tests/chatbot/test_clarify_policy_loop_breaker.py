"""CLARIFY-POLICY S1 (AC-1..AC-7): the loop breaker. UAC:
documentation/plans/chatbot/clarify-policy-acceptance-criteria.md.

Unit half: the pure functions of the new module `app/services/chatbot/clarify_policy.py`
(fails with ImportError until it exists). Engine half: the bare DO period ask ("my deliveries",
no subject, no dates: still a question after S4, AC-19) repeated through the real engine.
AC-3 and AC-4 (a different subject / an answer in between restarts the count) are unit-level only: after S4 / S3 no ask that
names a subject is still a repeatable question, so no engine path exists to drive AC-3; for AC-4 the answer turn carries the customer onto the next
bare ask, which then names a subject and is no longer the repeated question.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text

from app.services.chatbot import do_ask
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_do_ask_range_engine import dealer  # noqa: F401 - fixture
from tests.chatbot.test_engine import stub_access  # noqa: F401 - fixture

PHONE = "+60110000526"  # `test_dealer_eta_stock_routing._seed`'s contact


def _policy():
    from app.services.chatbot import clarify_policy

    return clarify_policy


# --- unit: ask_fingerprint / should_break --------------------------------------------------- #


def test_ac2_the_fingerprint_is_deterministic():
    p = _policy()
    assert p.ask_fingerprint("period", "date_range", None) == p.ask_fingerprint("period", "date_range", None)


def test_ac2_the_subject_is_compared_case_insensitively_and_trimmed():
    p = _policy()
    assert p.ask_fingerprint("attachment", "type", "ZZT100") == p.ask_fingerprint("attachment", "type", "  zzt100 ")


def test_ac2_a_different_kind_parameter_or_subject_is_a_different_fingerprint():
    p = _policy()
    base = p.ask_fingerprint("attachment", "type", "ZZT100")
    assert base != p.ask_fingerprint("period", "type", "ZZT100")
    assert base != p.ask_fingerprint("attachment", "date_range", "ZZT100")
    assert base != p.ask_fingerprint("attachment", "type", "ZZT200")
    assert p.ask_fingerprint("period", "date_range", None) != p.ask_fingerprint("period", "date_range", "ZZT100")


def test_ac1_breaks_only_when_the_last_two_asks_equal_the_current_one():
    p = _policy()
    f = p.ask_fingerprint("period", "date_range", None)
    assert p.should_break([f, f], f) is True
    assert p.should_break(["older", f, f], f) is True


def test_ac1_one_earlier_ask_or_none_does_not_break():
    p = _policy()
    f = p.ask_fingerprint("period", "date_range", None)
    assert p.should_break([], f) is False
    assert p.should_break([f], f) is False


def test_ac3_a_different_subject_restarts_the_count():
    p = _policy()
    a = p.ask_fingerprint("attachment", "type", "ZZT100")
    b = p.ask_fingerprint("attachment", "type", "ZZT200")
    assert p.should_break([a, a], b) is False
    assert p.should_break([a, b], a) is False


def test_ac4_a_non_clarification_reply_in_between_resets_the_count():
    p = _policy()
    f = p.ask_fingerprint("period", "date_range", None)
    assert p.should_break([f, None], f) is False
    assert p.should_break([None, f, f], f) is True  # the two most recent are what count


def test_ac2_wording_never_enters_the_check():
    """Two asks worded differently but sharing kind / parameter / subject are the same ask: the
    fingerprint takes no text at all, so there is nothing for wording to change."""
    import inspect

    params = list(inspect.signature(_policy().ask_fingerprint).parameters)
    assert params == ["kind", "param", "subject"], params


# --- engine: the bare DO period ask three times ---------------------------------------------- #

BARE = verdict(domain_hint="order", intent_hint="check_order", entities=[])


def _is_period_ask(reply: str) -> bool:
    return reply.startswith("Which period")


def _third_reply(console) -> str:
    first = console.say("my deliveries", BARE)
    second = console.say("my deliveries", BARE)
    assert _is_period_ask(first) and _is_period_ask(second), (first, second)  # asked twice is allowed
    return console.say("my deliveries", BARE)


def test_ac1_ac5_the_third_identical_period_ask_is_a_handover_not_the_question(dealer):  # noqa: F811
    third = _third_reply(dealer)
    assert not _is_period_ask(third), third
    lowered = third.lower()
    assert "escalate" in lowered or "refer to your salesman" in lowered, third


def test_ac6_with_escalation_barred_the_handover_is_the_refer_line(dealer, session_factory):  # noqa: F811
    db = session_factory()
    db.execute(text("UPDATE respond_contacts SET escalation_allowed = false WHERE phone_number = :p"), {"p": PHONE})
    db.commit()
    third = _third_reply(dealer)
    assert not _is_period_ask(third), third
    assert "Please refer to your salesman." in third, third
    assert "escalate" not in third.lower(), third


def test_ac1_the_breaking_reply_is_not_a_clarification_and_arms_no_pending(dealer):  # noqa: F811
    third = _third_reply(dealer)
    assert not _is_period_ask(third), third
    state = dealer.state or {}
    assert not (state.get("open_question") or {}), state.get("open_question")
