"""CLARIFY-POLICY AC-130..AC-132 (Q5; rows `generic_menu` / `entities_only`, seed `default`): a message with a subject but no domain gets an answer for the
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


def test_ac130_a_resolved_code_with_no_domain_gets_an_answer_and_the_something_else_line(console):
    reply = console.say(CODE, verdict(domain_hint=None, entities=[entity(CODE, hint="product")]))
    assert CODE in reply, reply
    assert reply.endswith(f"Something else? Tell me what you need about {CODE}."), reply
    assert "What would you like me to know?" not in reply, reply


def test_ac130_the_reply_is_data_about_the_subject_not_the_entities_only_sentence(console):
    reply = console.say(CODE, verdict(domain_hint=None, entities=[entity(CODE, hint="product")]))
    assert not reply.startswith("I have "), reply
    assert "Are you asking about any of these?" not in reply, reply


def test_ac131_no_subject_and_no_domain_keeps_todays_menu(console):
    reply = console.say("hello there", verdict(domain_hint=None, entities=[]))
    assert "Something else? Tell me what you need about" not in reply, reply
    assert reply.strip(), reply  # regression guard only: the bare turn goes to the casual clarifier today


def test_ac132_with_the_rows_set_to_ask_todays_entities_only_reply_returns(console, session_factory):
    from tests.chatbot.test_clarify_policy_table import _write_all_rows

    _write_all_rows(session_factory(), generic_menu={"mode": "ask"}, entities_only={"mode": "ask"})
    reply = console.say(CODE, verdict(domain_hint=None, entities=[entity(CODE, hint="product")]))
    assert reply == f"I have {CODE}. What would you like me to know?", reply



def test_ac130_a_bare_code_the_parser_types_unknown_still_gets_the_subject_answer(console):
    """Live check 13 Oct: the live parser reads a bare code as `message_type: unknown` with no
    domain and a resolved, confident `canonical_code` (the casual lane today)."""
    reply = console.say(CODE, verdict(message_type="unknown", domain_hint=None, entities=[entity(CODE, hint="product", canonical_code=CODE)]))
    assert CODE in reply, reply
    assert reply.endswith(f"Something else? Tell me what you need about {CODE}."), reply
