"""PV2 R5 slice G, the fetch half (UAC R5-8): the customer scope PV2 hands to the v1 business fetch.

One real `engine.run_turn` per case (the harness of test_redesign_r1_engine.py: the PV2 turn module faked, lanes real).
The seam patched is `lanes.business.run_fetch` (lanes/business/__init__.py:1354), wrapped to record the
`payload["ctx"]["customer_scope"]` it was called with, then run for real. v1-handled turns must keep today's scope.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.services.chatbot.lanes import business
from app.services.chatbot.pv2.state import Held
from tests.chatbot.pv2 import _register_envelopes as E
from tests.chatbot.pv2.carry_fakes import mk_state
from tests.chatbot.pv2.test_core_engine_hook import FakeResult, fake_turn  # noqa: F401 - fixture
from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2, _verdict
from tests.chatbot.test_customer_scope_lane import _link_customers
from tests.chatbot.test_engine import CONTACT_ID, seeded, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact


def _spy(monkeypatch):
    seen: list = []
    real = business.run_fetch

    def wrapper(payload, *a, **k):
        ctx = payload.get("ctx") if isinstance(payload, dict) else None
        seen.append((ctx or {}).get("customer_scope"))
        return real(payload, *a, **k)

    monkeypatch.setattr(business, "run_fetch", wrapper)
    return seen


def _turn(session_factory, monkeypatch, fake_turn, *, v2, link=None, salesperson=False):
    _seed_contact(session_factory, variables={})
    if link:
        _link_customers(session_factory, link)
    if salesperson:
        db = session_factory()
        db.execute(__import__("sqlalchemy").text(
            "INSERT INTO sales_agents (id, sales_agent, contact_id, company_id, is_active) "
            "SELECT gen_random_uuid(), :n, id, '00000000-0000-0000-0000-000000000001', true "
            "FROM respond_contacts WHERE respond_io_id = :c"), {"n": f"ZZTAG{uuid.uuid4().hex[:6]}", "c": str(CONTACT_ID)})
        db.commit()
    verdict = _verdict(domain_hint="order", intent_hint="check_order", order_status=None, entities=[],
                       date_filter_start="2026-09-01", date_filter_end="2026-09-30")
    if v2:
        _go_v2(session_factory)
        state = mk_state(Held(domain="order", sid="s1"))
        state.next_sid = 2
        fake_turn(FakeResult(verdict=verdict, state=state))
    seen = _spy(monkeypatch)
    import json as _j

    rendered = E.present_response(E.ORDERS_TOOL, _j.dumps(E.ORDERS_RAW))
    _run_turn(session_factory, monkeypatch, qf=verdict, text_body="my orders",
              msg_id=f"ZZT-gs-{uuid.uuid4().hex[:8]}", attributes=E.ALL_REVEALS, matches={}, mcp_response=rendered)
    return seen


def test_g5_a_pv2_turn_for_an_unlinked_contact_fetches_with_an_enforced_empty_scope(session_factory, monkeypatch, fake_turn):
    seen = _turn(session_factory, monkeypatch, fake_turn, v2=True)
    assert seen, "the business fetch ran"
    scope = seen[-1] or {}
    assert scope.get("enforced") is True and not scope.get("ids"), scope


def test_g5_a_pv2_turn_for_a_linked_dealer_fetches_with_their_own_ids_enforced(session_factory, monkeypatch, fake_turn):  # guard: passes today
    seen = _turn(session_factory, monkeypatch, fake_turn, v2=True, link="ZZT GS OWN")
    scope = seen[-1] or {}
    assert scope.get("enforced") is True and len(scope.get("ids") or []) == 1, scope


def test_g5_a_pv2_turn_for_a_salesperson_with_no_links_is_not_enforced(session_factory, monkeypatch, fake_turn):  # guard: passes today
    seen = _turn(session_factory, monkeypatch, fake_turn, v2=True, salesperson=True)
    assert seen
    assert not (seen[-1] or {}).get("enforced"), seen[-1]


def test_g5_a_v1_turn_for_an_unlinked_contact_keeps_its_scope(session_factory, monkeypatch, fake_turn):  # guard: passes today
    seen = _turn(session_factory, monkeypatch, fake_turn, v2=False)
    assert seen
    assert not (seen[-1] or {}).get("enforced"), seen[-1]
