"""PV2 R5 fix round 3: an unlinked contact's PV2 turn that v1's scope gate (or the fetch layer) refuses must still get a
non-empty reply, with no other customer's name. Real `engine.run_turn`, PV2 turn module faked (as test_g5 / test_g6)."""
from __future__ import annotations

import json
import uuid

from tests.chatbot.pv2 import _register_envelopes as E
from tests.chatbot.pv2.carry_fakes import mk_state
from tests.chatbot.pv2.test_core_engine_hook import FakeResult, fake_turn  # noqa: F401 - fixture
from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2, _verdict
from tests.chatbot.test_customer_scope_lane import _hanlim_services, _spied, _ent
from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from app.services.chatbot.pv2.state import Held


def _go(session_factory, monkeypatch, fake_turn, verdict, body, **kw):
    _seed_contact(session_factory, variables={})  # no customer links, no office type
    _go_v2(session_factory)
    state = mk_state(Held(domain="order", sid="s1"))
    state.next_sid = 2
    fake_turn(FakeResult(verdict=verdict, state=state))
    result, captured = _run_turn(session_factory, monkeypatch, qf=verdict, text_body=body,
                                 msg_id=f"ZZT-f3-{uuid.uuid4().hex[:8]}", attributes=E.ALL_REVEALS, **kw)
    return result, captured


def test_f3_a_customer_word_for_an_unlinked_contact_on_a_pv2_turn_gets_a_non_empty_reply_without_the_name(
        session_factory, monkeypatch, fake_turn):  # guard if it passes today
    services, _looked = _spied(_hanlim_services())
    verdict = _verdict(domain_hint="order", intent_hint="check_order", order_status=None, entities=[_ent("hanlim")])
    result, captured = _go(session_factory, monkeypatch, fake_turn, verdict, "outstanding for hanlim",
                           resolve_services=services)
    text = ((result.reply or {}).get("text") or "").strip()
    assert text, f"empty reply, status {result.status}"
    assert "HANLIM" not in text.upper(), text


def test_f3_an_order_list_for_an_unlinked_contact_on_a_pv2_turn_gets_a_non_empty_refusal(
        session_factory, monkeypatch, fake_turn):  # guard if it passes today
    verdict = _verdict(domain_hint="order", intent_hint="check_order", order_status=None, entities=[],
                       self_reference=True, date_filter_start="2026-09-01", date_filter_end="2026-09-30")
    rendered = E.present_response(E.ORDERS_TOOL, json.dumps(E.ORDERS_RAW))
    result, captured = _go(session_factory, monkeypatch, fake_turn, verdict, "my orders in september",
                           matches={}, mcp_response=rendered)
    text = ((result.reply or {}).get("text") or "").strip()
    assert text, f"empty reply, status {result.status}"
    for debtor in ("DELUXE HOME CENTRE", "HANLIM TRADING"):
        assert debtor not in text
