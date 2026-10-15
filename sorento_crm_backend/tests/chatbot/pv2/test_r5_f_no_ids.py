"""PV2 R5 slice F (UAC R5-7): no PV2 reply text contains a UUID or an internal key. Red tests, no LLM.

Each group drives one way a reply is built with an id where a name should be: (a) the did-you-mean list whose
option key is a UUID, (b) the size-miss reply whose held subject key is a UUID, (c) a report reply whose row key is
a customer UUID (and one whose printed row name is the id), (d) the final text `engine._pv2_direct_reply` sends.
A label with no human name falls back to its printed code, never the id.
"""
from __future__ import annotations

import re
from datetime import date

import pytest

from app.services.chatbot.pv2 import clarify_copy, reports, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import Period, ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, ResolvedRef, dump_state, new_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held, mk_state, out, spec
from tests.chatbot.pv2.test_core_engine_hook import FakeResult, _set_contact_mode, fake_turn  # noqa: F401 - fixture
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    _envelope,
    seeded,
    stub_access,
    stub_parser,
)

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PID_1 = "3b9f1c2e-7a41-4d6b-8e02-5c7d9a1f4b60"
PID_2 = "c81d5e90-2f3a-4b77-9a14-6e0b3d8c2f51"
CID = "6f1c2a9e-0b7d-4c55-9a3e-1d2f3b4c5d6e"


def _no_ids(text: str):
    assert not UUID.search(text), text


# ------------------------------------------------------------------ (a) did-you-mean

class DymResolver(FakeResolver):
    def resolve_entities(self, ents, *, held):
        res = super().resolve_entities(ents, held=held)
        for r in res:
            r.did_you_mean = True
        return res


def _dym_options():
    return [{"key": k, "label": lb, "payload": {"kind": "product", "key": k, "label": lb}}
            for k, lb in ((PID_1, "SRTWC4410"), (PID_2, "SRTWC4411"))]


def test_f1_the_did_you_mean_list_prints_the_codes_never_an_option_key_that_is_an_id():
    r = DymResolver({"entities": {"product|srtwc441": {"status": "ambiguous", "options": _dym_options()}}})
    st, acts = apply_turn(new_state(NOW), out(ask(act="new_topic", domain="inventory",
                                                  entities=[entity("product", phrase="srtwc441")])),
                          resolver=r, now=NOW, can_offer_handover=False)
    act = acts[0]
    assert isinstance(act, ClarifyAct) and act.pending.kind == "clarify_did_you_mean", type(act).__name__
    _no_ids(act.pending.asked)
    _no_ids(clarify_copy.render(act.pending, "en"))
    assert "SRTWC4410" in act.pending.asked and "SRTWC4411" in act.pending.asked, act.pending.asked


# ------------------------------------------------------------------ (b) size miss

class SizeResolver(FakeResolver):
    def resolve_specs(self, specs, *, held):
        res = super().resolve_specs(specs, held=held)
        for r in res:
            r.sizes_on_file = ["Length 250, 600 mm"]
        return res


def test_f2_the_size_miss_names_the_product_by_its_code_never_its_id():
    st = mk_state(held("inventory", {}), turn_no=3)
    st.held.refs = {"product": [ResolvedRef(kind="product", key=PID_1, label="SRTWC4410")]}
    r = SizeResolver({"specs": {"|300": {"status": "none"}}})
    parsed = out(ask(act="refines_held", domain="inventory", specs=[spec(number=300, unit="mm")]))
    _, acts = apply_turn(st, parsed, resolver=r, now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    _no_ids(acts[0].pending.asked)
    assert "SRTWC4410" in acts[0].pending.asked, acts[0].pending.asked


# ------------------------------------------------------------------ (c) report rows

def _report() -> ReportSpec:
    return ReportSpec(dataset="sales", measures=["amount"], group_by=["customer"], top_n_per_group=None, filters=[],
                      period=Period(preset="last_month", n=None, date_from=None, date_to=None), compare_to=None,
                      compare_period=None, grain=None, threshold=None, sort=None, top_n=10, basis="delivered")


class _Rep:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _report_turn(row_name, text):
    row = reports.ReportOption(key=row_name, label=row_name,
                               payload={reports.ROW_PAYLOAD: {"dimension": "customer", "keys": [CID], "label": row_name}})

    def report_answer(spec_, refs, today, picked, incoming):
        return _Rep(text=text, plan=_Rep(spec=spec_, status="run"), rows=[row], pending=None, frame=None)

    ctx = turn.TurnV2Context(
        text="top customers last month", now=NOW, raw_state=None, profile=turn.MemoryProfile(audience="staff"),
        parse=lambda m, u: out(ask(act="new_topic", domain="sales", report=_report())), resolver=FakeResolver({}),
        load_exchanges=lambda since: [], report_answer=report_answer,
        granted_reveals=frozenset({turn.SALES_REPORT_GRANT}),
    )
    return turn._run(ctx)


def test_f3_guard_a_report_row_keyed_by_a_customer_id_prints_the_name():  # guard: passes today
    res = _report_turn("ABC TRADING SDN BHD", "Top customers\n1. ABC TRADING SDN BHD RM 10.00")
    assert res.route == "report", res.route
    _no_ids(res.direct_reply)
    for row in res.state.last_rows:
        _no_ids(row.label)


def test_f3_a_report_row_whose_printed_name_is_the_id_never_reaches_the_reply_or_the_pick_list():
    res = _report_turn(CID, f"Top customers\n1. {CID} RM 10.00")
    assert res.route == "report", (res.route, res.direct_reply)
    _no_ids(res.direct_reply)
    for row in res.state.last_rows:
        _no_ids(row.label)


# ------------------------------------------------------------------ (d) engine _pv2_direct_reply

def test_f4_the_text_the_engine_sends_for_a_pv2_direct_reply_holds_no_id(session_factory, seeded, stub_parser,
                                                                       stub_access, fake_turn):
    from app.services.chatbot import engine as engine_mod

    fake_turn(FakeResult(direct_reply=f"Which one?\n1. {PID_1}\n2. SRTWC4411 ({PID_2})", branch_kind="clarify_menu"))
    stub_parser(error=AssertionError("v1 parser must not run on a v2 turn"))
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert result.branch_kind == "clarify_menu", result.branch_kind
    _no_ids(result.reply["text"])
    for action in result.actions:
        if action.get("kind") == "send_message":
            _no_ids(action["text"])
    assert "SRTWC4411" in result.reply["text"]
