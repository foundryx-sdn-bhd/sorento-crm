"""PV2-FIX round 3: an ask whose portal_form is set answers in domain portal_link."""
from __future__ import annotations

from datetime import date

from app.services.chatbot.head.parser import assert_emission
from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import Lookup

from tests.chatbot.pv2.carry_fakes import FakeResolver, ask, mk_state, out

TODAY = date(2026, 10, 14)


def _run(domain, portal_form):
    base = ask().model_dump()
    base.update(domain=domain, portal_form=portal_form)
    parsed = out(Ask.model_validate(base))
    return apply_turn(mk_state(), parsed, resolver=FakeResolver({}))


def test_r3_portal_form_overrides_the_written_domain():
    state, actions = _run("forms", "sponsorship_form")
    assert len(actions) == 1 and isinstance(actions[0], Lookup)
    assert actions[0].domain == "portal_link"
    assert state.held.domain == "portal_link"


def test_r3_bridge_sends_portal_link_domain_and_form_to_v1():
    state, actions = _run("forms", "sponsorship_form")
    v = bridge.to_v1_verdict(state, actions[0], today=TODAY)
    assert_emission(v)
    assert v["domain_hint"] == "portal_link"
    assert v["portal_form"] == "sponsorship_form"
    assert v["intent_hint"] == bridge.INTENT_BY_DOMAIN["portal_link"]


def test_r3_forms_without_portal_form_stays_forms():
    state, actions = _run("forms", None)
    assert isinstance(actions[0], Lookup)
    assert actions[0].domain == "forms" and state.held.domain == "forms"
