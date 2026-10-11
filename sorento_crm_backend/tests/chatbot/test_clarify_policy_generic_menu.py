"""CLARIFY-POLICY S6 (AC-27, AC-28): a message with a subject but no domain gets an answer for the
subject's default domain plus "Something else? Tell me what you need about <subject>." instead of
the topic menu; with no subject and no domain the menu stays. UAC:
documentation/plans/chatbot/clarify-policy-acceptance-criteria.md.

Named-team subjects (the other half of AC-27's subject) are not covered: the UAC does not say what
a team's default domain answers.
"""
from __future__ import annotations

import pytest

from app.main import app
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_dealer_eta_stock_routing import CODE, Console, _seed
from tests.chatbot.test_engine import stub_access  # noqa: F401 - fixture


@pytest.fixture
def console(session_factory, monkeypatch, stub_access):
    _seed(session_factory, dealer=False, salesperson=False, shipments=True)
    try:
        yield Console(session_factory, monkeypatch, stub_access)
    finally:
        app.dependency_overrides.clear()


def test_ac27_a_resolved_code_with_no_domain_gets_an_answer_and_the_something_else_line(console):
    reply = console.say(CODE, verdict(domain_hint=None, entities=[entity(CODE, hint="product")]))
    assert CODE in reply, reply
    assert reply.endswith(f"Something else? Tell me what you need about {CODE}."), reply
    assert "What would you like me to know?" not in reply, reply


def test_ac27_the_reply_is_data_about_the_subject_not_the_entities_only_sentence(console):
    reply = console.say(CODE, verdict(domain_hint=None, entities=[entity(CODE, hint="product")]))
    assert not reply.startswith("I have "), reply
    assert "Are you asking about any of these?" not in reply, reply


def test_ac28_no_subject_and_no_domain_keeps_todays_menu(console):
    reply = console.say("hello there", verdict(domain_hint=None, entities=[]))
    assert "Something else? Tell me what you need about" not in reply, reply
    assert reply.strip(), reply  # regression guard only: the bare turn goes to the casual clarifier today
