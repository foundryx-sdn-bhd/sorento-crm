"""PV2 R5 slice G round 2 (UAC R5-8): an unlinked non-staff contact reaches NO customer data downstream.

G (2d4fd519d) only changed the scope value in ctx; the guards behind it read `enforced` with empty ids as "no
restriction". These tests watch what actually leaves: the MCP tool calls of the business fetch (seam: the `mcp_call`
double of `_run_turn`, `tests/chatbot/test_outstanding_lane.py:_capturing_mcp`, wired through
`lanes/business/services._mcp_call`) and the text of the reply.
Part 1: the engine path (real `engine.run_turn`, PV2 turn module faked as in test_redesign_r1_engine.py).
Part 2: PV2's own report path (real `turn.run_turn_v2`, parse stubbed; seam `lanes.business.services._mcp_call`).
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
from app.services.chatbot.pv2.schema import Period, ReportSpec
from app.services.chatbot.pv2.state import Held
from app.services.chatbot.turn.state import Profile
from tests.chatbot.pv2 import _register_envelopes as E
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, mk_state, out
from tests.chatbot.pv2.test_core_engine_hook import FakeResult, fake_turn  # noqa: F401 - fixture
from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2, _verdict
from tests.chatbot.test_customer_scope_lane import _link_customers, _other_customer
from tests.chatbot.test_engine import CONTACT_ID, seeded, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY, _seed_so

OTHER_NAME = "ZZT NOBODYS LEDGER SDN BHD"
REPORT_TOOLS = {"crm_report_ask", "crm_sales_report"}


# ------------------------------------------------------------------ part 1: DO and SO lists through the engine

def _engine_turn(session_factory, monkeypatch, fake_turn, verdict, *, link=None, body="my orders in september"):
    _seed_contact(session_factory, variables={})
    own = _link_customers(session_factory, link) if link else []
    other = _other_customer(session_factory, OTHER_NAME)
    _seed_so(session_factory, "SO733001", customer_id=other, lines=[(2, 0)], debtor_name=OTHER_NAME,
             order_date="2026-09-12")
    _go_v2(session_factory)
    state = mk_state(Held(domain="order", sid="s1"))
    state.next_sid = 2
    fake_turn(FakeResult(verdict=verdict, state=state))
    rendered = E.present_response(E.ORDERS_TOOL, json.dumps(E.ORDERS_RAW))
    result, captured = _run_turn(session_factory, monkeypatch, qf=verdict, text_body=body,
                                 msg_id=f"ZZT-g2-{uuid.uuid4().hex[:8]}", attributes=[*E.ALL_REVEALS, OUTSTANDING_KEY],
                                 matches={}, mcp_response=rendered)
    return result, captured, own


def _do_verdict():
    return _verdict(domain_hint="order", intent_hint="check_order", order_status=None, entities=[],
                    self_reference=True, date_filter_start="2026-09-01", date_filter_end="2026-09-30")


def _so_verdict():
    return _verdict(domain_hint="order", intent_hint="check_order", order_status=None, entities=[], document=["SO"],
                    self_reference=True, so_list=True, date_filter_start="2026-09-01", date_filter_end="2026-09-30")


def _reply(result):
    return (result.reply or {}).get("text") or ""


@pytest.mark.parametrize("make,body", [(_do_verdict, "my orders in september"), (_so_verdict, "all sales orders for september")],
                         ids=["delivery orders", "sales orders"])
def test_g6_an_unlinked_contact_reaches_no_customer_scoped_tool_and_no_rows(session_factory, monkeypatch, fake_turn, make, body):
    result, captured, _ = _engine_turn(session_factory, monkeypatch, fake_turn, make(), body=body)
    called = {name for name, _args in captured}
    assert not (called & CUSTOMER_SCOPED_TOOLS), captured
    text = _reply(result)
    assert OTHER_NAME not in text and "SO733001" not in text, text
    for debtor in ("DELUXE HOME CENTRE", "HANLIM TRADING"):
        assert debtor not in text, text


def test_g6_a_linked_dealer_still_gets_the_tool_called_with_their_own_customer_ids(session_factory, monkeypatch, fake_turn):  # guard: passes today
    result, captured, own = _engine_turn(session_factory, monkeypatch, fake_turn, _do_verdict(), link="ZZT G2 OWN")
    scoped = [args for name, args in captured if name in CUSTOMER_SCOPED_TOOLS]
    assert scoped, captured
    assert [str(i) for i in scoped[0].get("customer_ids") or []] == [str(i) for i in own], scoped[0]


# ------------------------------------------------------------------ part 2: PV2 reports

def _report() -> ReportSpec:
    return ReportSpec(dataset="sales", measures=["amount"], group_by=["customer"], top_n_per_group=None, filters=[],
                      period=Period(preset="this_month", n=None, date_from=None, date_to=None), compare_to=None,
                      compare_period=None, grain=None, threshold=None, sort=None, top_n=10, basis="delivered")


def _chat_report() -> ReportSpec:
    return ReportSpec(dataset="chat", measures=["asks"], group_by=["customer"], top_n_per_group=None, filters=[],
                      period=Period(preset="this_month", n=None, date_from=None, date_to=None), compare_to=None,
                      compare_period=None, grain=None, threshold=None, sort=None, top_n=5, basis=None)


def _report_turn(session_factory, monkeypatch, *, tier=None, link=None, grants=(turn.SALES_REPORT_GRANT,), spec=None):
    _seed_contact(session_factory, variables={})
    if link:
        _link_customers(session_factory, link)
    called: list = []

    def mcp(name, args):
        called.append((name, dict(args)))
        return json.dumps(E.REPORT_ASK_ENVELOPE)

    cfg = SimpleNamespace(keys=SimpleNamespace(entity_keys={}, attachment_types=[]), index_sha="sha",
                          prompt_version=1, version_id="v", cache_key="k")
    call = types.ModuleType("app.services.chatbot.pv2.call")
    call.resolve_v2_config = lambda db, override_version_id=None: cfg
    parsed = out(ask(act="new_topic", domain="chat_analytics" if spec is not None else "sales", report=spec or _report()))
    call.parse_v2 = lambda config, history, memory_block, text: SimpleNamespace(output=parsed, usage={}, dropped=[])
    import app.services.chatbot.pv2 as pv2_pkg

    monkeypatch.setitem(sys.modules, "app.services.chatbot.pv2.call", call)
    monkeypatch.setattr(pv2_pkg, "call", call, raising=False)
    monkeypatch.setattr("app.services.chatbot.head.access.default_space_id", lambda db: None)
    monkeypatch.setattr("app.services.chatbot.head.access._granted_field_reveal_keys",
                        lambda db, contact_id, space_id: list(grants))
    monkeypatch.setattr("app.services.chatbot.lanes.business.services._mcp_call", lambda db=None: mcp)
    monkeypatch.setattr(turn, "load_exchanges", lambda db, **kw: [])
    hctx = SimpleNamespace(
        session_factory=session_factory, override_version_id=None, contact_respond_id=str(CONTACT_ID),
        envelope=SimpleNamespace(ingress="whatsapp", is_test=False), dry_run=False, session_block={"session_vars": {}},
        profile=Profile(tier=tier), text="sales by customer this month", now=NOW, memory_intake=None, state_in=None)
    return turn.run_turn_v2(hctx), called


def test_g7_an_unlinked_contact_with_the_sales_report_grant_runs_no_report_and_sees_no_names(session_factory, monkeypatch):
    # R6 Q5(a): a contact holding the grant is exempt. This pins the R5-8 HOLD itself: a contact with no report grant
    # asks a CHAT report (no sales-grant refusal fires first), so only `_refused_report` stops it.
    res, called = _report_turn(session_factory, monkeypatch, grants=(), spec=_chat_report())
    assert not ({n for n, _ in called} & REPORT_TOOLS), called
    text = res.direct_reply or ""
    assert "ABC SDN BHD" not in text and "XYZ" not in text and "RM 15" not in text, text
    assert text.strip(), "the hold answers in words"


def test_g7_office_staff_still_get_the_report(session_factory, monkeypatch):  # guard: passes today
    res, called = _report_turn(session_factory, monkeypatch, tier="office")
    assert {n for n, _ in called} & REPORT_TOOLS, called


def test_g7_a_linked_dealer_still_gets_the_report_run(session_factory, monkeypatch):  # guard: passes today
    res, called = _report_turn(session_factory, monkeypatch, link="ZZT G2 LINKED")
    assert {n for n, _ in called} & REPORT_TOOLS, called
