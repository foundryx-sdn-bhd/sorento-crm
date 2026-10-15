"""PV2 R6 Q5(a) security should-fix: the granted-report exemption must not outlive a re-plan.

An unlinked contact holding `chatbot.analytics` gets its turn-wide customer scope lifted because the FIRST plan is
chat-only. If the resolver re-entry (engine `turn_apply` with the resolver's results) turns the plan into an order fetch,
the held refusal must still fire: no orders / outstanding tool is called. Seam: `engine.turn_apply` (as
tests/chatbot/test_chat_report_scope_leak.py `_replan_to_orders`) and the `mcp_call` double of `_run_turn`.
Plus the nit: a numbered domain menu offers only domains the contact is granted.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.services.chatbot.pv2.state import Held
from tests.chatbot.pv2.carry_fakes import mk_state
from tests.chatbot.pv2.test_core_engine_hook import FakeResult, fake_turn  # noqa: F401 - fixture
from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2
from tests.chatbot.test_chat_report_lane import SEP, _chat, _e
from tests.chatbot.test_chat_report_scope_leak import _replan_to_orders
from tests.chatbot.test_customer_scope_lane import _other_customer
from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY

NAME = "ZZT REPLAN FOREIGN SDN BHD"
DOC_TOOLS = {"crm_order_management_orders_list", "crm_outstanding_report", "crm_order_management_orders_by_product_list"}


def _replanned_turn(session_factory, monkeypatch, fake_turn, grants):
    from app.services.chatbot.analytics import chat_report_extractor

    full = {"group_by": "topic", "measure": "asks", "subject": None, "top_n": 5, "date_from": "2026-09-01",
            "date_to": "2026-09-30", "askers": [], "category_words": [], "brand_words": []}
    monkeypatch.setattr(chat_report_extractor, "extract_chat_report", lambda db, text, *a, **k: dict(full))
    _seed_contact(session_factory, variables={})
    foreign = _other_customer(session_factory, NAME)
    verdict = _chat(_e(NAME, "customer"), group_by="topic", measure="asks", asks=None, topic_reset=None,
                    **SEP)
    _go_v2(session_factory)
    state = mk_state(Held(domain="chat_analytics", sid="s1"))
    state.next_sid = 2
    fake_turn(FakeResult(verdict=verdict, state=state))
    calls = _replan_to_orders(monkeypatch)
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=verdict, text_body=f"top 5 questions from {NAME} this week",
        msg_id=f"ZZT-rp-{uuid.uuid4().hex[:8]}", attributes=list(grants),
        matches={NAME: {"uuid": foreign, "entity_type": "customer", "canonical_code": "ZZT-RP"}},
        mcp_response={"has_result": False})
    return result, captured, calls, foreign


def test_rp_a_chat_plan_replanned_to_orders_for_an_unlinked_granted_contact_calls_no_order_tool(session_factory, monkeypatch, fake_turn):
    result, captured, calls, foreign = _replanned_turn(session_factory, monkeypatch, fake_turn, ["chatbot.analytics", OUTSTANDING_KEY])
    assert any(n > 1 for n in calls), ("the re-plan did not happen", calls)
    assert not ({n for n, _ in captured} & DOC_TOOLS), captured
    text = (result.reply or {}).get("text") or ""
    assert text.strip(), "an empty reply is sent"
    assert foreign not in str(captured) and NAME not in text, (text, captured)


def test_rp_guard_without_the_chat_grant_the_replan_is_held(session_factory, monkeypatch, fake_turn):  # guard: passes today
    result, captured, calls, foreign = _replanned_turn(session_factory, monkeypatch, fake_turn, [OUTSTANDING_KEY])
    assert not ({n for n, _ in captured} & DOC_TOOLS), captured


# ------------------------------------------------------------------ nit: a domain menu offers only granted domains

def _domain_menu(granted):
    from app.services.chatbot.pv2 import turn
    from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, out

    parsed = out(ask(act="unclear", domain=None,
                     entities=[__import__("tests.chatbot.pv2.carry_fakes", fromlist=["entity"]).entity("product", phrase="SRTWC9022")]),
                 clarify={"needed": True, "about": "domain", "phrase": None})
    r = FakeResolver({"entities": {"product|SRTWC9022": {"status": "exact", "refs": [
        {"kind": "product", "key": "SRTWC9022", "label": "SRTWC9022"}]}}})
    ctx = turn.TurnV2Context(
        text="and that one?", now=NOW, raw_state=None, profile=turn.MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=r, load_exchanges=lambda since: [], granted_reveals=frozenset(granted))
    res = turn._run(ctx)
    assert res.route == "clarify" and res.state.pending is not None, res.route
    return [o.key for o in res.state.pending.options]


def test_nit_a_numbered_domain_menu_hides_the_domains_the_contact_is_not_granted():
    from app.services.chatbot.lanes.business.answer import DOMAIN_GRANT_REQUIRED

    keys = _domain_menu([])
    assert keys, "the menu has options"
    assert not (set(keys) & set(DOMAIN_GRANT_REQUIRED)), keys


def test_nit_guard_a_contact_holding_the_grants_is_offered_those_domains():  # guard: passes today
    from app.services.chatbot.lanes.business.answer import DOMAIN_GRANT_REQUIRED

    keys = _domain_menu(DOMAIN_GRANT_REQUIRED.values())
    assert "sales" in keys, keys
