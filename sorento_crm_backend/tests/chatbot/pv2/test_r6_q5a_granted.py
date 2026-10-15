"""PV2 R6 behaviour 6 (owner ruling CA-3 Q5(a)): an unlinked non-staff contact holding the report grant gets reports
like staff on a PV2 turn. v1's rule (#1648): `granted_report_turn` = every fetch is chat_analytics plus
`chatbot.analytics`, or order_status in SALES_FIGURE_STATUSES plus `sales_orders.sales_report`. Documents stay held.

Seams as R5: the `mcp_call` double of `_run_turn` (engine path) and `lanes.business.services._mcp_call` (PV2 report path).
"""
from __future__ import annotations

import json
import sys
import types
import uuid
from types import SimpleNamespace

import pytest

from app.services.chatbot.lanes.business.fetch import CUSTOMER_SCOPED_TOOLS
from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.state import Held
from app.services.chatbot.turn.state import Profile
from tests.chatbot.pv2 import _register_envelopes as E
from tests.chatbot.pv2.carry_fakes import NOW, ask, mk_state, out
from tests.chatbot.pv2.test_core_engine_hook import FakeResult, fake_turn  # noqa: F401 - fixture
from tests.chatbot.pv2.test_r5_g2_no_reach import REPORT_TOOLS, _report
from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2, _verdict
from tests.chatbot.test_chat_report_lane import SEP, _chat, _e
from tests.chatbot.test_customer_scope_lane import _other_customer
from tests.chatbot.test_engine import CONTACT_ID, seeded, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact

CHAT_GRANT = "chatbot.analytics"
SALES_GRANT = "sales_orders.sales_report"
CHAT_BODY = {"result_type": "report_ask", "has_result": True,
             "response": "Top customers by questions\n1. ABC SDN BHD 12\n2. XYZ TRADING 7"}


def _pv2(session_factory, fake_turn, verdict):
    _go_v2(session_factory)
    state = mk_state(Held(domain="order", sid="s1"))
    state.next_sid = 2
    fake_turn(FakeResult(verdict=verdict, state=state))


def _fake_extractor(monkeypatch, group_by="customer"):
    from app.services.chatbot.analytics import chat_report_extractor

    full = {"group_by": group_by, "measure": "asks", "subject": None, "top_n": 5, "date_from": "2026-09-01",
            "date_to": "2026-09-30", "askers": [], "category_words": [], "brand_words": []}
    monkeypatch.setattr(chat_report_extractor, "extract_chat_report", lambda db, text, *a, **k: dict(full))


def _chat_turn(session_factory, monkeypatch, fake_turn, grants, *, entities=(), matches=None):
    _fake_extractor(monkeypatch)
    _seed_contact(session_factory, variables={})
    verdict = _chat(*entities, group_by="customer", measure="asks", asks=None, topic_reset=None, **SEP)
    _pv2(session_factory, fake_turn, verdict)
    result, captured = _run_turn(session_factory, monkeypatch, qf=verdict, text_body="which customers ask us the most in september",
                                 msg_id=f"ZZT-q5-{uuid.uuid4().hex[:8]}", attributes=list(grants), matches=matches or {},
                                 mcp_response=json.dumps(CHAT_BODY))
    return ((result.reply or {}).get("text") or ""), captured


# ------------------------------------------------------------------ chat reports

def test_q5_an_unlinked_contact_holding_the_chat_grant_gets_the_customer_grouped_chat_report(session_factory, monkeypatch, fake_turn):
    text, captured = _chat_turn(session_factory, monkeypatch, fake_turn, [CHAT_GRANT])
    assert [n for n, _ in captured if n == "crm_report_ask"], captured
    assert "ABC SDN BHD" in text, text


def test_q5_the_customer_picker_works_for_that_contact_a_named_customer_is_not_refused(session_factory, monkeypatch, fake_turn):
    seeded_id = _other_customer(session_factory, "ZZT Q5 ZEBRA SDN BHD")
    text, captured = _chat_turn(
        session_factory, monkeypatch, fake_turn, [CHAT_GRANT], entities=[_e("ZZT Q5 ZEBRA SDN BHD", "customer")],
        matches={"ZZT Q5 ZEBRA SDN BHD": {"uuid": seeded_id, "entity_type": "customer", "canonical_code": "ZZT-Z"}})
    assert "isn't under your account" not in text, text
    assert [n for n, _ in captured if n == "crm_report_ask"], captured


def test_q5_guard_without_the_chat_grant_the_contact_stays_held(session_factory, monkeypatch, fake_turn):  # guard: passes today
    text, captured = _chat_turn(session_factory, monkeypatch, fake_turn, [])
    assert not [a for n, a in captured if n == "crm_report_ask"], captured
    assert "ABC SDN BHD" not in text and "XYZ TRADING" not in text, text


# ------------------------------------------------------------------ sales reports

def _pv2_report_turn(session_factory, monkeypatch, grants, *, tier=None, spec=None, link=None):
    _seed_contact(session_factory, variables={})
    if link:
        from tests.chatbot.test_customer_scope_lane import _link_customers

        _link_customers(session_factory, link)
    called: list = []

    def mcp(name, args):
        called.append((name, dict(args)))
        return json.dumps(E.REPORT_ASK_ENVELOPE)

    cfg = SimpleNamespace(keys=SimpleNamespace(entity_keys={}, attachment_types=[]), index_sha="sha",
                          prompt_version=1, version_id="v", cache_key="k")
    call = types.ModuleType("app.services.chatbot.pv2.call")
    call.resolve_v2_config = lambda db, override_version_id=None: cfg
    parsed = out(ask(act="new_topic", domain="chat_analytics" if spec is not None and spec.dataset == "chat" else "sales", report=spec or _report()))
    call.parse_v2 = lambda config, history, memory_block, text: SimpleNamespace(output=parsed, usage={}, dropped=[])
    import app.services.chatbot.pv2 as pv2_pkg

    monkeypatch.setitem(sys.modules, "app.services.chatbot.pv2.call", call)
    monkeypatch.setattr(pv2_pkg, "call", call, raising=False)
    monkeypatch.setattr("app.services.chatbot.head.access.default_space_id", lambda db: None)
    monkeypatch.setattr("app.services.chatbot.head.access._granted_field_reveal_keys", lambda db, contact_id, space_id: list(grants))
    monkeypatch.setattr("app.services.chatbot.lanes.business.services._mcp_call", lambda db=None: mcp)
    monkeypatch.setattr(turn, "load_exchanges", lambda db, **kw: [])
    hctx = SimpleNamespace(
        session_factory=session_factory, override_version_id=None, contact_respond_id=str(CONTACT_ID),
        envelope=SimpleNamespace(ingress="whatsapp", is_test=False), dry_run=False, session_block={"session_vars": {}},
        profile=Profile(tier=tier), text="sales by customer this month", now=NOW, memory_intake=None, state_in=None)
    return turn.run_turn_v2(hctx), called


def test_q5_an_unlinked_contact_holding_the_sales_report_grant_gets_the_pv2_sales_report(session_factory, monkeypatch):
    res, called = _pv2_report_turn(session_factory, monkeypatch, [SALES_GRANT])
    assert {n for n, _ in called} & REPORT_TOOLS, called
    assert (res.direct_reply or "").strip()


def test_q5_an_unlinked_contact_holding_the_sales_report_grant_gets_a_sales_figure_status_through_the_v1_lane(
        session_factory, monkeypatch, fake_turn):
    _seed_contact(session_factory, variables={})
    verdict = _verdict(domain_hint="order", intent_hint="check_order", order_status="sales_report", entities=[],
                       date_filter_start="2026-09-01", date_filter_end="2026-09-30")
    _pv2(session_factory, fake_turn, verdict)
    result, captured = _run_turn(session_factory, monkeypatch, qf=verdict, text_body="how much did we sell in september",
                                 msg_id=f"ZZT-q5s-{uuid.uuid4().hex[:8]}", attributes=[SALES_GRANT], matches={},
                                 mcp_response=json.dumps(E.REPORT_ASK_ENVELOPE))
    assert [n for n, _ in captured if n in REPORT_TOOLS | {"crm_top_selling_report"}], captured
    assert "isn't under your account" not in ((result.reply or {}).get("text") or "")


def test_q5_guard_without_the_sales_report_grant_the_pv2_report_stays_held(session_factory, monkeypatch):  # guard: passes today
    res, called = _pv2_report_turn(session_factory, monkeypatch, [])
    assert not ({n for n, _ in called} & REPORT_TOOLS), called


# ------------------------------------------------------------------ documents stay held whatever the grants

def test_q5_guard_a_granted_contact_asking_for_documents_is_still_held(session_factory, monkeypatch, fake_turn):  # guard: passes today
    _seed_contact(session_factory, variables={})
    verdict = _verdict(domain_hint="order", intent_hint="check_order", order_status=None, entities=[], self_reference=True,
                       date_filter_start="2026-09-01", date_filter_end="2026-09-30")
    _pv2(session_factory, fake_turn, verdict)
    rendered = E.present_response(E.ORDERS_TOOL, json.dumps(E.ORDERS_RAW))
    result, captured = _run_turn(session_factory, monkeypatch, qf=verdict, text_body="my orders in september",
                                 msg_id=f"ZZT-q5d-{uuid.uuid4().hex[:8]}", attributes=[*E.ALL_REVEALS, CHAT_GRANT, SALES_GRANT],
                                 matches={}, mcp_response=rendered)
    assert not ({n for n, _ in captured} & CUSTOMER_SCOPED_TOOLS), captured
    text = (result.reply or {}).get("text") or ""
    assert "DELUXE HOME CENTRE" not in text and "HANLIM TRADING" not in text, text


# ------------------------------------------------------------------ the picker on the PV2 report path itself

def _named_customer_report():
    from app.services.chatbot.pv2.schema import Filter

    return _report().model_copy(update={"group_by": [], "filters": [
        Filter(dimension="customer", op="in", keys=["ZZT Q5 BORNEO SDN BHD"], values=[])]})


def test_q5_the_pv2_report_resolves_a_named_customer_for_a_granted_unlinked_contact_and_runs_scoped_to_it(session_factory, monkeypatch):
    cid = _other_customer(session_factory, "ZZT Q5 BORNEO SDN BHD")
    res, called = _pv2_report_turn(session_factory, monkeypatch, [SALES_GRANT], spec=_named_customer_report())
    ran = [a for n, a in called if n in REPORT_TOOLS]
    assert ran, (called, res.direct_reply)
    assert cid in json.dumps(ran, default=str), ran


def test_q5_guard_the_same_named_customer_report_without_the_grant_stays_held(session_factory, monkeypatch):  # guard: passes today
    cid = _other_customer(session_factory, "ZZT Q5 BORNEO SDN BHD")
    res, called = _pv2_report_turn(session_factory, monkeypatch, [], spec=_named_customer_report())
    assert not ({n for n, _ in called} & REPORT_TOOLS), called
    assert "BORNEO" not in (res.direct_reply or "").upper()


# ------------------------------------------------------------------ the hold on the PV2 report path, and each grant opens only its own dataset

def _chat_spec():
    from tests.chatbot.pv2.test_r5_g2_no_reach import _chat_report

    return _chat_report()


def test_q5_k39_an_ungranted_unlinked_contact_asking_a_pv2_chat_report_is_refused_by_the_hold(session_factory, monkeypatch):
    """No grant at all: no sales-grant refusal fires first for a chat report, so only `_refused_report`'s own grant
    check stops it. Remove that check and the report runs."""
    res, called = _pv2_report_turn(session_factory, monkeypatch, [], spec=_chat_spec())
    assert not ({n for n, _ in called} & REPORT_TOOLS), called
    text = res.direct_reply or ""
    assert "own account" in text, text
    assert "ABC SDN BHD" not in text and "XYZ" not in text, text


def test_q5_k40_the_sales_report_grant_does_not_open_a_chat_report(session_factory, monkeypatch):
    res, called = _pv2_report_turn(session_factory, monkeypatch, [SALES_GRANT], spec=_chat_spec())
    assert not ({n for n, _ in called} & REPORT_TOOLS), called
    assert "own account" in (res.direct_reply or ""), res.direct_reply


def test_q5_k40_the_chat_grant_does_not_open_a_sales_report(session_factory, monkeypatch):  # guard: passes today (the sales grant check fires)
    res, called = _pv2_report_turn(session_factory, monkeypatch, [CHAT_GRANT])
    assert not ({n for n, _ in called} & REPORT_TOOLS), called
    assert (res.direct_reply or "").strip()


def test_q5_k40_guard_the_chat_grant_opens_a_chat_report(session_factory, monkeypatch):  # guard: passes today
    res, called = _pv2_report_turn(session_factory, monkeypatch, [CHAT_GRANT], spec=_chat_spec())
    assert {n for n, _ in called} & REPORT_TOOLS, (called, res.direct_reply)
