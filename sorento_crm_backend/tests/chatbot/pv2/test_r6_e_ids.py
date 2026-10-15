"""PV2 R6 behaviour 5 (class E): no id reaches v1 as text and no id leaves in a v1-rendered reply. Red tests.

E1 pure: `bridge.to_v1_verdict` / `bridge.focus_from_v2` from a hand-built state. E2 engine: one real `engine.run_turn`
with the PV2 turn module faked and a v1 lane rendering a text that holds ids (seam: `lanes.business.run_fetch`).
"""
from __future__ import annotations

import re
import uuid
from datetime import date

import pytest

from app.services.chatbot import engine as engine_mod  # noqa: F401
from app.services.chatbot.lanes import business
from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import Lookup, RegisterEntry, ResolvedRef
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, out
from tests.chatbot.pv2.test_core_engine_hook import FakeResult, fake_turn  # noqa: F401 - fixture
from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2, _verdict
from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PID = "7c2e9b10-4d3a-4f6e-8a15-2b9d0c7e1f33"
KNOWN = "0e5a7d92-1c48-4b0f-9d26-6f3a8b1c4e70"
UNKNOWN = "b4d81f06-93a2-4c7e-a5b0-1e6c2d9f7a48"


def _lookup(refs: dict[str, list[ResolvedRef]], domain="inventory"):
    st = mk_state(mk_held(domain, {}), turn_no=3)
    st.held.refs = refs
    parsed = out(ask(act="refines_held", domain=domain))
    _, acts = apply_turn(st, parsed, resolver=FakeResolver({}), now=NOW, can_offer_handover=False)
    act = next(a for a in acts if isinstance(a, Lookup))
    return st, act


def _sent(refs, domain="inventory"):
    st, act = _lookup(refs, domain)
    v = bridge.to_v1_verdict(st, act, today=date(2026, 10, 15))
    return v["entities"], bridge.focus_from_v2(act.held)


def _texts(ent):
    return {ent.get("raw"), ent.get("canonical_code")} - {None}


# ------------------------------------------------------------------ E1 the bridge sends a code or a label, never a key that is not a code

@pytest.mark.parametrize("key,label", [("water_closet", "Water Closet"), ("basin_pedestal", "Pedestal Basin")])
def test_e1_a_product_type_key_goes_to_v1_as_its_label(key, label):
    ents, _ = _sent({"product_type": [ResolvedRef(kind="product_type", key=key, label=label)]})
    row = next(e for e in ents if e["hint"] == "product_type" or e["raw"] in (key, label))
    assert key not in _texts(row), row
    assert label in _texts(row), row


def test_e1_a_product_whose_key_is_an_id_goes_to_v1_as_its_printed_code():
    ents, _ = _sent({"product": [ResolvedRef(kind="product", key=PID, label="SRTWC4410")]})
    sent = [t for e in ents for t in _texts(e)]
    assert PID not in sent and "SRTWC4410" in sent, ents


def test_e1_a_report_row_drill_with_an_id_key_goes_to_v1_as_its_label():
    ents, _ = _sent({"sales_agent": [ResolvedRef(kind="sales_agent", key=PID, label="FANNY I")]}, domain="sales")
    sent = [t for e in ents for t in _texts(e)]
    assert PID not in sent and "FANNY I" in sent, ents


def test_e1_brand_keys_into_the_v1_focus_are_codes_or_labels_never_ids():
    _, focus = _sent({"brand": [ResolvedRef(kind="brand", key=PID, label="ZEBRA BRAND")]})
    assert PID not in focus.brands and "ZEBRA BRAND" in focus.brands, focus.brands


def test_e1_focus_rows_never_carry_an_id_as_their_text():
    _, focus = _sent({"product": [ResolvedRef(kind="product", key=PID, label="SRTWC4411")]})
    for row in focus.products:
        assert PID not in (row.get("raw"), row.get("canonical_code")), row


def test_e1_guard_a_product_code_key_is_sent_untouched():  # guard: passes today
    ents, focus = _sent({"product": [ResolvedRef(kind="product", key="SRTWC4412-SH", label="SRTWC4412-SH Black")]})
    assert "SRTWC4412-SH" in [t for e in ents for t in _texts(e)]


def test_e1_guard_a_customer_with_an_id_key_keeps_v1s_carried_pick_shape():  # guard: passes today
    ents, _ = _sent({"customer": [ResolvedRef(kind="customer", key=PID, label="ZZT ACME SDN BHD")]}, domain="order")
    row = next(e for e in ents if e["hint"] == "customer")
    assert row["raw"] == "ZZT ACME SDN BHD" and row["uuid"] == PID


# ------------------------------------------------------------------ E2 a v1-rendered reply on a PV2 turn is scrubbed

def _engine_turn(session_factory, monkeypatch, fake_turn, *, v2):
    _seed_contact(session_factory, variables={})
    verdict = _verdict(domain_hint="order", intent_hint="check_order", order_status=None, entities=[],
                       date_filter_start="2026-09-01", date_filter_end="2026-09-30")
    if v2:
        _go_v2(session_factory)
        state = mk_state(mk_held("order", {}), turn_no=3)
        state.register = [RegisterEntry(id="R1", kind="customer", key=KNOWN, label="HANLIM TRADING SDN BHD",
                                        parent=None, subject=None, turn=2)]
        fake_turn(FakeResult(verdict=verdict, state=state))
    line = f"Order for {KNOWN} and {UNKNOWN} with SRTWC4413 ({UNKNOWN})"
    monkeypatch.setattr(business, "run_fetch", lambda payload, **kw: business._fixed_reply(line))
    result, _ = _run_turn(session_factory, monkeypatch, qf=verdict, text_body="my orders in september",
                          msg_id=f"ZZT-e2-{uuid.uuid4().hex[:8]}", attributes=[], matches={})
    return result


def _all_text(result):
    sent = [a.get("text") or "" for a in result.actions if a.get("kind") == "send_message"]
    return [(result.reply or {}).get("text") or "", *sent]


def test_e2_a_v1_rendered_reply_on_a_pv2_turn_holds_no_id_in_the_reply_or_the_sent_actions(session_factory, monkeypatch, fake_turn):
    result = _engine_turn(session_factory, monkeypatch, fake_turn, v2=True)
    texts = _all_text(result)
    assert any(texts), "a reply was sent"
    for t in texts:
        assert not UUID_RE.search(t), t


def test_e2_a_known_id_prints_its_label_an_unknown_one_goes_and_a_product_code_stays(session_factory, monkeypatch, fake_turn):
    result = _engine_turn(session_factory, monkeypatch, fake_turn, v2=True)
    text = (result.reply or {}).get("text") or ""
    assert "HANLIM TRADING SDN BHD" in text and "SRTWC4413" in text, text
    assert "()" not in text, text


def test_e2_guard_a_v1_only_turn_is_unchanged(session_factory, monkeypatch, fake_turn):  # guard: passes today
    result = _engine_turn(session_factory, monkeypatch, fake_turn, v2=False)
    assert KNOWN in ((result.reply or {}).get("text") or "")
