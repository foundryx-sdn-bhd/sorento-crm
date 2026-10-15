"""PV2 R5 fix round 4 (UAC R5-8): the remaining paths that read a held-to-no-customer scope as unrestricted, plus the
grant pre-check that still judges a numbered answer by the parser's domain tag."""
from __future__ import annotations

import json
import uuid
from datetime import date

import pytest

from app.models.order import SalesOrder, SalesOrderLine
from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.state import dump_state
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, out
from tests.chatbot.pv2.test_r5_fix2_picks import _products, _which_line_menu
from tests.chatbot.pv2.test_r5_d_doc_lines import _resolver
from tests.chatbot.pv2.test_r5_g2_no_reach import REPORT_TOOLS, _report_turn  # noqa: F401
from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2  # noqa: F401
from tests.chatbot.pv2.test_core_engine_hook import fake_turn  # noqa: F401 - fixture
from tests.chatbot.test_top_selling_round5 import cat, route  # noqa: F401 - fixtures
from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY

OTHER = "ZZT FOURTH PASS LEDGER SDN BHD"


# ------------------------------------------------------------------ 3 a scope-read error still refuses in words

def test_f4_3_a_scope_read_error_for_a_non_staff_contact_on_a_pv2_report_turn_gets_a_non_empty_refusal(
        session_factory, monkeypatch):
    from app.services.chatbot.pv2 import hook as pv2_hook

    def boom(*a, **k):
        raise RuntimeError("scope read failed")

    monkeypatch.setattr(pv2_hook, "customer_scope", boom)
    res, called = _report_turn(session_factory, monkeypatch, grants=())  # R6 Q5(a): the hold is for a contact without the grant
    assert not ({n for n, _ in called} & REPORT_TOOLS), called
    assert (res.direct_reply or "").strip(), "an empty reply is sent"


# ------------------------------------------------------------------ 4 carried SO-list ids under an enforced empty scope

def test_f4_4_carried_so_list_ids_for_another_customer_return_no_rows_under_an_enforced_empty_scope():
    from app.services.chatbot.lanes.business import run_fetch
    from app.services.chatbot.lanes.business.services import FetchServices

    with S.resolve_session() as db:
        cust = S.customer(db, "ZZT-4TH-1", OTHER)
        so = SalesOrder(id=str(uuid.uuid4()), so_number="SO744001", debtor_name=OTHER, customer_id=cust.id,
                        company_id=DEFAULT_COMPANY_ID, order_date=date(2026, 9, 12), status="open")
        db.add(so)
        db.flush()
        p = S.product(db, "SRTWQ7440")
        db.add(SalesOrderLine(id=str(uuid.uuid4()), sales_order_id=so.id, product_id=p.id, qty_ordered=3, qty_delivered=0))
        db.flush()
        payload = {
            "_exit_kind": "continue", "gate": {"compatible_entities": []},
            "ctx": {
                "contact": {"id": "900000008"},
                "customer_scope": {"enforced": True, "ids": [], "linked": []},
                "access": {"attributes": [OUTSTANDING_KEY]},
                "parse": {"output": {
                    "domain_hint": "order", "intent_hint": "check_order", "message_type": "business_query",
                    "entities": [], "so_list": True, "self_reference": True,
                    "outstanding_carried_customer_ids": [str(cust.id)],
                    "date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30"}},
            },
        }

        def _no_tool(name, args):
            raise AssertionError("no tool for the SO list")

        fragment = run_fetch(payload, services=FetchServices(mcp_call=_no_tool), db=db)
        text = json.dumps(fragment, default=str)
        assert "SO744001" not in text and OTHER not in text, text


# ------------------------------------------------------------------ 5 a numbered answer is not judged by its domain tag

@pytest.mark.parametrize("tag", ["sales", "purchase_order"])
def test_f4_5_a_number_on_the_stock_menu_tagged_with_an_ungranted_domain_runs_the_stock_lookup_not_a_grant_refusal(tag):
    st1 = _which_line_menu()
    menu = [o.label for o in st1.pending.options]
    parsed = out(ask(act="new_topic", domain=tag, row_pick=2))
    ctx = turn.TurnV2Context(
        text="2", now=NOW, raw_state=dump_state(st1), profile=turn.MemoryProfile(audience="staff"),
        parse=lambda m, u: parsed, resolver=_resolver(), load_exchanges=lambda since: [],
        granted_reveals=frozenset(),
    )
    res = turn._run(ctx)
    assert res.route == "lookup", (res.route, res.direct_reply)
    assert res.direct_reply is None, res.direct_reply
    assert _products(res.state) == [menu[1]], (_products(res.state), menu)


# ------------------------------------------------------------------ 1 chat report: the asker rule must not relax a held-to-no-customer scope

CHAT_TOOL = "crm_report_ask"


def _chat_engine_turn(session_factory, monkeypatch, fake_turn, *, salesperson=False, attributes=("chatbot.analytics",)):
    from app.services.chatbot.pv2.state import Held
    from tests.chatbot.pv2.carry_fakes import mk_state
    from tests.chatbot.pv2.test_core_engine_hook import FakeResult
    from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2
    from tests.chatbot.test_chat_report_lane import SEP, _chat
    from tests.chatbot.test_chat_report_scope_leak import _salesperson_user
    from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact

    from app.services.chatbot.analytics import chat_report_extractor

    full = {"group_by": "customer", "measure": "asks", "subject": None, "top_n": 5, "date_from": "2026-09-01",
            "date_to": "2026-09-30", "askers": [], "category_words": [], "brand_words": []}
    monkeypatch.setattr(chat_report_extractor, "extract_chat_report", lambda db, text, *a, **k: dict(full))
    _seed_contact(session_factory, variables={})
    if salesperson:
        _salesperson_user(session_factory)
    _go_v2(session_factory)
    verdict = _chat(group_by="customer", measure="asks", asks=None, topic_reset=None, **SEP)
    state = mk_state(Held(domain="chat_analytics", sid="s1"))
    state.next_sid = 2
    fake_turn(FakeResult(verdict=verdict, state=state))
    body = {"result_type": "report_ask", "response": "Top customers by questions\n1. ABC SDN BHD 12\n2. XYZ TRADING 7",
            "has_result": True}
    result, captured = _run_turn(session_factory, monkeypatch, qf=verdict, text_body="which customers ask us the most in september",
                                 msg_id=f"ZZT-f4-{uuid.uuid4().hex[:8]}", attributes=list(attributes), matches={},
                                 mcp_response=json.dumps(body))
    return result, captured


def test_f4_1_an_unlinked_contact_with_the_chat_grant_gets_no_customer_grouped_chat_report(session_factory, monkeypatch, fake_turn):
    # R6 Q5(a): a contact holding the grant is exempt, so this pins the hold for one WITHOUT it.
    result, captured = _chat_engine_turn(session_factory, monkeypatch, fake_turn, attributes=())
    text = (result.reply or {}).get("text") or ""
    assert not [a for n, a in captured if n == CHAT_TOOL and not a.get("scope_customer_ids")], captured
    assert "ABC SDN BHD" not in text and "XYZ TRADING" not in text, text


def test_f4_1_guard_a_salesperson_without_links_still_gets_the_chat_report(session_factory, monkeypatch, fake_turn):  # guard: passes today
    result, captured = _chat_engine_turn(session_factory, monkeypatch, fake_turn, salesperson=True)
    assert [n for n, _ in captured if n == CHAT_TOOL], captured


# ------------------------------------------------------------------ 2 top selling: the who question names no customer for an unlinked contact

def _who_turn(session_factory, monkeypatch, fake_turn, *, v2):
    from app.services.chatbot.pv2.state import Held
    from tests.chatbot.pv2.carry_fakes import mk_state
    from tests.chatbot.pv2.test_core_engine_hook import FakeResult
    from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2
    from tests.chatbot.test_top_selling_round5 import _ask, _e, _turn

    verdict = _ask(top_n=100, rank_by="amount", entities=[_e("water closet", "category"), _e("contactz", "customer")])
    if v2:
        _go_v2(session_factory)
        verdict = {**verdict, "asks": None, "topic_reset": None}
        state = mk_state(Held(domain="order", sid="s1"))
        state.next_sid = 2
        fake_turn(FakeResult(verdict=verdict, state=state))
        # R6 Q5(a): a contact holding the sales-report grant is exempt, so the PV2 case pins the hold without it.
        from tests.chatbot.test_outstanding_lane import _resolve_services, _run_turn

        result, captured = _run_turn(
            session_factory, monkeypatch, qf=verdict, text_body="top 100 hot selling water closet by contactz",
            msg_id=f"ZZT-f4-2-{uuid.uuid4().hex[:8]}", attributes=["sales_orders.outstanding"],
            resolve_services=_resolve_services({}))
        return str((result.reply or {}).get("text") or ""), list(captured)
    return _turn(session_factory, monkeypatch, verdict, "top 100 hot selling water closet by contactz")


def test_f4_2_the_who_question_for_an_unlinked_contact_on_a_pv2_turn_prints_no_customer_name(
        session_factory, monkeypatch, fake_turn, cat, route):
    text, _captured = _who_turn(session_factory, monkeypatch, fake_turn, v2=True)
    assert "CONTACTZ X" not in text and "Do you mean customer" not in text, text


def test_f4_2_guard_the_same_turn_on_v1_keeps_its_who_question(session_factory, monkeypatch, fake_turn, cat, route):  # guard: passes today
    from tests.chatbot.test_top_selling_round5 import FANNY_WHO

    text, _captured = _who_turn(session_factory, monkeypatch, fake_turn, v2=False)
    assert text == FANNY_WHO, text
