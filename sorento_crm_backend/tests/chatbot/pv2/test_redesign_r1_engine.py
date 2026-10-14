"""PV2 redesign R1 through the engine: a PV2 turn whose v1 lane printed a list ends with the register on the saved
PV2 state (`session_vars["pv2"]["register"]`). One real `engine.run_turn` per case, the PV2 turn module faked (its
verdict is the bridge's, its state a real ConversationState), the lanes and presenters real.

UAC R1-1/2 (a DO list with its product lines), R1-5 (an SO card), R1-8 (a direct reply keeps the previous entries),
R1-12 (a v1-mode turn writes no register)."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from sorento_crm_mcp.presenters import present_response
from tests.chatbot.pv2 import _register_envelopes as E
from app.services.chatbot.pv2.state import Held
from tests.chatbot.pv2.carry_fakes import mk_state
from tests.chatbot.pv2.test_core_engine_hook import FakeResult, fake_turn  # noqa: F401 - fixture
from tests.chatbot.test_customer_scope_lane import _link_customers
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY, _seed_so


def _go_v2(session_factory):
    db = session_factory()
    db.execute(text("update respond_contacts set chatbot_parser_mode = 'v2' where respond_io_id = :c"),
               {"c": str(CONTACT_ID)})
    db.commit()


def _saved(session_factory) -> dict:
    row = session_factory().execute(
        text("select session_vars from respond_contacts where respond_io_id = :c"), {"c": str(CONTACT_ID)}).first()
    raw = row.session_vars if row is not None else {}
    return (json.loads(raw) if isinstance(raw, str) else raw) or {}


def _verdict(**kw):
    return _parser_output(asks=None, topic_reset=None, **kw)


def test_a_do_list_turn_saves_orders_and_their_product_lines_in_the_register(session_factory, monkeypatch, fake_turn):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, "ZZT OWN A")
    _go_v2(session_factory)
    verdict = _verdict(domain_hint="order", intent_hint="check_order", order_status=None, entities=[],
                       date_filter_start="2026-09-01", date_filter_end="2026-09-30")
    # R2: subject is the stable sid (UAC R2-2)
    state = mk_state(Held(domain="order", sid="s4"))
    state.next_sid = 5
    fake_turn(FakeResult(verdict=verdict, state=state))
    rendered = present_response(E.ORDERS_TOOL, json.dumps(E.ORDERS_RAW))
    result, _ = _run_turn(session_factory, monkeypatch, qf=verdict, text_body="my orders",
                          msg_id=f"ZZT-reg-{uuid.uuid4().hex[:8]}", attributes=E.ALL_REVEALS, matches={},
                          mcp_response=rendered)
    assert "202609-2012" in ((result.reply or {}).get("text") or ""), result.reply
    register = _saved(session_factory)["pv2"]["register"]
    orders = [e for e in register if e["kind"] == "delivery_order"]
    assert [e["label"] for e in orders] == ["202609-2012", "202609-2013"]
    kids = [e["label"] for e in register if e["parent"] == orders[0]["id"]]
    assert kids == ["SRTWB1096", "SRTWT7203"]
    assert all(e["subject"] == "s4" for e in register)  # R2: subject is the stable sid (UAC R2-2)


def test_an_so_card_turn_saves_the_sales_order_in_the_register(session_factory, monkeypatch, fake_turn):
    _seed_contact(session_factory, variables={})
    (own,) = _link_customers(session_factory, "ZZT OWN A")
    _seed_so(session_factory, "SO422057", customer_id=own, lines=[(2, 0)], order_date="2026-09-21")
    _go_v2(session_factory)
    verdict = _verdict(
        domain_hint="order", intent_hint="check_order", order_status=None,
        entities=[{"raw": "SO422057", "hint": "order_number", "canonical_code": None, "current_message": True,
                   "confident": True}])
    fake_turn(FakeResult(verdict=verdict, state=mk_state()))
    result, _ = _run_turn(session_factory, monkeypatch, qf=verdict, text_body="SO422057 status?",
                          msg_id=f"ZZT-reg-{uuid.uuid4().hex[:8]}", attributes=[OUTSTANDING_KEY], matches={},
                          mcp_response={"has_result": False, "items": []})
    assert "SO422057" in ((result.reply or {}).get("text") or ""), result.reply
    register = _saved(session_factory)["pv2"]["register"]
    assert [(e["kind"], e["label"]) for e in register] == [("sales_order", "SO422057")]
    assert not any("-" in e["label"] and len(e["label"]) == 36 for e in register)


def test_a_pv2_direct_reply_keeps_the_previous_replys_entries(session_factory, seeded, stub_parser, stub_access, fake_turn):
    from app.services.chatbot import engine as engine_mod

    from app.services.chatbot.pv2.state import RegisterEntry

    state = mk_state()
    state.register = [RegisterEntry(id="R1", kind="delivery_order", key="202609-2012", label="202609-2012",
                                    parent=None, subject="S1", turn=2)]
    fake_turn(FakeResult(direct_reply="Which one do you mean?", branch_kind="clarify_menu", state=state))
    stub_parser()
    stub_access()
    _go_v2(session_factory)
    engine_mod.run_turn(_envelope(), session_factory=session_factory)
    register = _saved(session_factory)["pv2"]["register"]
    assert [e["label"] for e in register] == ["202609-2012"]


def test_a_v1_mode_turn_writes_no_register(session_factory, seeded, stub_parser, stub_access):
    from app.services.chatbot import engine as engine_mod

    stub_parser()
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert result.status == "done"
    assert "pv2" not in _saved(session_factory)
