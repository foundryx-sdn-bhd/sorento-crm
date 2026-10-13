"""Reviewer round 1 findings, each pinned (pure, FakeResolver; reports monkeypatched)."""
from __future__ import annotations

import dataclasses
import sys
import types
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.chatbot.pv2 import answer, bridge, clarify_copy, reports, turn
from app.services.chatbot.pv2.apply import apply_turn, report_refs
from app.services.chatbot.pv2.schema import Answer, Filter, LowStock, Period, ReportSpec
from app.services.chatbot.pv2.state import (
    ClarifyAct,
    Held,
    Lookup,
    OfferHandover,
    ResolvedRef,
)
from app.services.chatbot.turn.state import Focus, Profile
from tests.chatbot.pv2.carry_fakes import (
    NOW,
    FakeResolver,
    ask,
    entity,
    held,
    mk_state,
    option,
    out,
    pending,
    spec,
)

TODAY = date(2026, 10, 13)


def _report(**kw) -> ReportSpec:
    base = dict(dataset="sales", measures=["amount"], group_by=[], top_n_per_group=None, filters=[],
                period=Period(preset="last_month", n=None, date_from=None, date_to=None), compare_to=None,
                compare_period=None, grain=None, threshold=None, sort=None, top_n=10, basis="delivered")
    base.update(kw)
    return ReportSpec(**base)


def _ref(kind, key, label=None):
    return ResolvedRef(kind=kind, key=key, label=label or key)


class _Rep:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# -- B1 / B2: report filters that do not settle are asked --------------------------------- #


def _filtered_turn(resolver):
    o = out(ask(act="new_topic", domain="sales", report=_report(filters=[
        Filter(dimension="customer", op="in", keys=["Ahmad"], values=[])])))
    return apply_turn(mk_state(), o, resolver=resolver, now=NOW), o


def test_b1_unsettled_report_filter_is_a_clarify_never_a_lookup_or_empty_reply():
    (state, actions), o = _filtered_turn(FakeResolver())
    assert isinstance(actions[0], ClarifyAct) and not isinstance(actions[0], Lookup)
    assert actions[0].pending.asked.strip()
    r = answer.build_result(state, actions, o, today=TODAY,
                            report_answer=lambda *a: pytest.fail("a report must not run unfiltered"))
    assert r.route == "clarify" and r.direct_reply and r.direct_reply.strip()


def test_b1_report_refs_map_warehouse_and_product_type_to_report_dimensions():
    h = Held(domain="sales", refs={
        "warehouse": [_ref("warehouse", "WH1", "Main")],
        "product_type": [_ref("product_type", "toilet", "Toilet")],
        "customer": [_ref("customer", "C1", "Ahmad")]})
    got = report_refs(h)
    assert set(got) == {"location", "category", "customer"}
    assert got["location"] == [{"kind": "warehouse", "key": "WH1", "label": "Main"}]
    assert got["category"] == [{"kind": "product_type", "key": "toilet", "label": "Toilet"}]


@pytest.mark.parametrize(
    "exc",
    [
        AttributeError("module 'app.services.chatbot.pv2.reports' has no attribute 'resolve_report_spec'"),
        NotImplementedError("no report resolver on this build"),
    ],
)
def test_b2_a_report_resolver_that_raises_does_not_crash_and_the_filter_is_said(exc):
    class Raises(FakeResolver):
        def resolve_report_spec(self, *a, **k):
            raise exc

    (state, actions), _ = _filtered_turn(Raises())
    assert isinstance(actions[0], ClarifyAct)
    assert "not available yet" in actions[0].pending.asked
    assert state.held.report is not None and not state.held.report.filters


def test_b2_an_unrelated_attribute_error_inside_the_resolver_is_not_swallowed():
    class Broken(FakeResolver):
        def resolve_report_spec(self, *a, **k):
            raise AttributeError("'NoneType' object has no attribute 'id'")

    with pytest.raises(AttributeError):
        _filtered_turn(Broken())


# -- B3: drill refusal and drill refs -------------------------------------------------------- #

ROW = option("1", "Ahmad Trading", {reports.ROW_PAYLOAD: {"dimension": "customer", "label": "Ahmad Trading", "keys": ["C1"]}})


def _runner():
    return answer.default_report_answer(mcp_call=lambda *a, **k: {}, contact_id="c", space_id=None, dealer=False)


def test_b3_a_refused_drill_says_the_v1_line_and_runs_nothing(monkeypatch):
    def refuse(*a, **k):
        raise reports.DrillRefused("line")

    monkeypatch.setattr(reports, "drill_row", refuse)
    monkeypatch.setattr(reports, "answer_report", lambda *a, **k: pytest.fail("must not run"))
    res = _runner()(_report(), {}, TODAY, ROW, None)
    assert res.text == "line"


def test_b3_drill_refs_land_on_held_through_build_result(monkeypatch):
    drilled = _report(filters=[Filter(dimension="customer", op="in", keys=["Ahmad Trading"], values=[])])
    extra = {"customer": [{"kind": "customer", "key": "C1", "label": "Ahmad Trading"}]}
    seen = {}

    def fake_answer(s, **k):
        seen.update(k)
        return SimpleNamespace(text="Drilled", plan=SimpleNamespace(spec=drilled, status="run"), rows=[], pending=None)

    monkeypatch.setattr(reports, "drill_row", lambda s, r, incoming=None: (drilled, extra))
    monkeypatch.setattr(reports, "answer_report", fake_answer)
    h = held("sales", {"brand": ["SORENTO"]}, report=_report())
    lk = Lookup(ask_index=0, domain="sales", held=h, ask=ask(act="refines_held", row_pick=1), row=ROW)
    r = answer.build_result(mk_state(h), [lk], out(ask()), today=TODAY, report_answer=_runner())
    assert r.direct_reply == "Drilled"
    assert [x.key for x in r.state.held.refs["customer"]] == ["C1"]
    assert [x.key for x in r.state.held.refs["brand"]] == ["SORENTO"]
    assert seen["refs"]["customer"] == extra["customer"]


# -- B4: the hook envelope is read by attribute ------------------------------------------------ #


def _hook(monkeypatch, envelope, dry_run=False):
    captured = {}
    cfg = SimpleNamespace(keys=SimpleNamespace(entity_keys={}, attachment_types=[]), index_sha="sha",
                          prompt_version=1, version_id="v", cache_key="k")
    call = types.ModuleType("app.services.chatbot.pv2.call")
    call.resolve_v2_config = lambda db, override_version_id=None: cfg
    call.parse_v2 = lambda config, history, memory_block, text: SimpleNamespace(
        output=out(ask(act="small_talk")), usage={}, dropped=[])
    res = types.ModuleType("app.services.chatbot.pv2.resolve")
    res.DbResolver = lambda db, pinned=None: FakeResolver()
    import app.services.chatbot.pv2 as pv2_pkg

    # `from app.services.chatbot.pv2 import call` reads the package attribute once the real
    # module is loaded (the engine imports it), so stub both the attribute and sys.modules.
    for name, mod in (("call", call), ("resolve", res)):
        monkeypatch.setitem(sys.modules, f"app.services.chatbot.pv2.{name}", mod)
        monkeypatch.setattr(pv2_pkg, name, mod, raising=False)
    monkeypatch.setattr("app.services.chatbot.head.access.default_space_id", lambda db: None)
    monkeypatch.setattr("app.services.chatbot.lanes.business.services._mcp_call", lambda db=None: (lambda *a, **k: {}))

    def fake_load(db, **kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(turn, "load_exchanges", fake_load)
    hctx = SimpleNamespace(
        session_factory=lambda: SimpleNamespace(close=lambda: None), override_version_id=None,
        contact_respond_id="r-1", envelope=envelope, dry_run=dry_run, session_block={"session_vars": {}},
        profile=Profile(), text="hi", now=NOW, memory_intake=None, state_in=None)
    turn.run_turn_v2(hctx)
    return captured


def test_b4_console_ingress_and_is_test_come_from_envelope_attributes(monkeypatch):
    got = _hook(monkeypatch, SimpleNamespace(ingress="console", is_test=True))
    assert got["ingress"] == "console" and got["is_test"] is True and got["contact_respond_id"] == "r-1"


def test_b4_non_console_and_dry_run(monkeypatch):
    got = _hook(monkeypatch, SimpleNamespace(ingress="whatsapp", is_test=False), dry_run=True)
    assert got["ingress"] == "whatsapp" and got["is_test"] is True
    got = _hook(monkeypatch, SimpleNamespace(ingress="whatsapp", is_test=False))
    assert got["is_test"] is False


# -- B5: a v1 escalation offer answered yes/no -------------------------------------------------- #


def _yes_no(key, v1_kind):
    h = held("inventory", {"product": ["A"]})
    picked = option(key, key, {"kind": "choice", "key": key, "v1_kind": v1_kind})
    lk = Lookup(ask_index=0, domain="inventory", held=h, pending_kind="v1_open_question", picked=picked)
    return bridge.to_v1_verdict(mk_state(h), lk, today=TODAY)


@pytest.mark.parametrize("v1_kind", ["team_pick", "member_offer", "company_pick"])
def test_b5_escalation_offer_yes_and_no(v1_kind):
    assert _yes_no("yes", v1_kind)["escalation"]["is_escalation_confirmation"] is True
    assert _yes_no("no", v1_kind)["escalation"]["is_escalation_confirmation"] is False


def test_b5_other_offer_kinds_leave_it_unset():
    assert _yes_no("yes", "product_pick")["escalation"]["is_escalation_confirmation"] is None


# -- S1: v1 question mirror keyed by position ---------------------------------------------------- #


def test_s1_mirror_keys_options_by_v1_position():
    v1 = SimpleNamespace(kind="product_pick", question="Which one?",
                         options=[{"label": "Alpha", "position": 2}, {"label": "Beta", "position": 5}])
    new = turn.mirror_v1_pending(mk_state(), v1)
    q = new.pending
    assert q.kind == "v1_open_question" and q.v1_kind == "product_pick"
    assert [o.key for o in q.options] == ["2", "5"] and [o.label for o in q.options] == ["Alpha", "Beta"]


# -- S2: carried vs named specs -------------------------------------------------------------------- #


def test_s2_carried_spec_is_not_current_named_spec_is():
    h = held("product_attachment", {"product": ["A"]}, specs=[("trap_type", "p_trap")])
    carried = bridge.to_v1_verdict(mk_state(h), Lookup(ask_index=0, domain=h.domain, held=h, ask=ask(act="refines_held")), today=TODAY)
    assert [e["current_message"] for e in carried["entities"] if e["hint"] == "specification"] == [False]
    named = bridge.to_v1_verdict(
        mk_state(h), Lookup(ask_index=0, domain=h.domain, held=h, ask=ask(act="refines_held", specs=[spec(attribute="trap_type", value_key="p_trap")])),
        today=TODAY)
    assert [e["current_message"] for e in named["entities"] if e["hint"] == "specification"] == [True]
    # product refs stay current either way
    assert [e["current_message"] for e in carried["entities"] if e["hint"] == "product"] == [True]


# -- S3: focus overlay ------------------------------------------------------------------------------- #


def test_s3_focus_keeps_v1_only_axes_and_replaces_subject():
    base = Focus(products=[{"canonical_code": "OLD"}], tier=["dealer"], tasks=("t1",), required_ask={"k": 1},
                 customers=[{"canonical_code": "OLDC"}], brands=["OLDB"])
    f = bridge.focus_from_v2(held("inventory", {"product": ["NEW"]}), base)
    assert f.tier == ["dealer"] and f.tasks == ("t1",) and f.required_ask == {"k": 1}
    assert [r["canonical_code"] for r in f.products] == ["NEW"]
    assert f.customers == [] and f.brands == []
    assert base.products == [{"canonical_code": "OLD"}]  # base untouched
    assert dataclasses.is_dataclass(f)


def test_s3_build_result_passes_the_base_focus_through():
    h = held("inventory", {"product": ["NEW"]})
    base = Focus(tier=["dealer"])
    r = answer.build_result(mk_state(h), [Lookup(ask_index=0, domain="inventory", held=h)], out(ask()), today=TODAY,
                            base_focus=base)
    assert r.focus.tier == ["dealer"]


# -- S4: low stock ------------------------------------------------------------------------------------ #


def test_s4_low_stock_ask_sets_intent_and_object():
    h = held("inventory", {"product_type": ["toilet"], "brand": ["SORENTO"]})
    a = ask(act="refines_held", low_stock=LowStock(group_by="category", all_categories=False))
    v = bridge.to_v1_verdict(mk_state(h), Lookup(ask_index=0, domain="inventory", held=h, ask=a), today=TODAY)
    assert v["intent_hint"] == "low_stock_report"
    assert v["low_stock"]["group_by"] == "category" and v["low_stock"]["all_categories"] is False
    assert v["low_stock"]["brands"] == ["SORENTO"]


# -- S5 / S11: misses on the pending question ----------------------------------------------------------- #

SPEC_Q = pending("clarify_spec_attribute", [option("dim_length", "Length"), option("dim_height", "Height")],
                 asked="Which 600 mm do you mean?")


@pytest.mark.parametrize("o", [
    out(ask(act="unclear")),
    out(ask(act="refines_held"), clarify={"needed": True, "phrase": "x", "about": "other"}),
], ids=["unclear_act", "clarify_needed"])
def test_s5_unclear_with_a_pending_reasks_it_then_offers_handover(o):
    state = mk_state(held("product_attachment", {"product": ["A"]}), pending_=SPEC_Q)
    s1, a1 = apply_turn(state, o, resolver=FakeResolver())
    assert isinstance(a1[0], ClarifyAct) and a1[0].reask is True
    assert s1.pending.kind == "clarify_spec_attribute" and s1.pending.misses == 1
    assert [x.key for x in s1.pending.options] == ["dim_length", "dim_height"]
    s2, a2 = apply_turn(s1, o, resolver=FakeResolver())
    assert isinstance(a2[0], OfferHandover) and a2[0].pending.kind == "offer_handover"


# -- S6 / M5: topics across several asks --------------------------------------------------------------- #

ENT = {"entities": {
    "product|SRTWC8066": {"status": "exact", "refs": [{"kind": "product", "key": "SRTWC8066", "label": "SRTWC8066"}]},
    "product|SRTWC286": {"status": "exact", "refs": [{"kind": "product", "key": "SRTWC286", "label": "SRTWC286"}]}}}


def test_s6_switch_back_in_a_later_ask_uses_the_topic_memory_printed():
    state = mk_state(held("product_attachment", {"product": ["SRTWC286"]}), topics=[held("sales", {"brand": ["SORENTO"]})])
    o = out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="SRTWC8066")]),
            ask(act="switch_back", switch_back_to="T1"))
    new, actions = apply_turn(state, o, resolver=FakeResolver(ENT))
    assert [a.type for a in actions] == ["lookup", "lookup"]
    assert new.held.domain == "sales" and [r.key for r in new.held.refs["brand"]] == ["SORENTO"]
    assert actions[1].domain == "sales"


def test_m5_a_later_refines_pushes_the_earlier_ask_onto_topics():
    o = out(ask(act="new_topic", domain="incoming", entities=[entity("product", phrase="SRTWC286")]),
            ask(act="refines_held", domain="inventory"))
    new, actions = apply_turn(mk_state(), o, resolver=FakeResolver(ENT))
    assert [a.domain for a in actions] == ["incoming", "inventory"]
    assert [t.domain for t in new.topics] == ["incoming"]
    assert [r.key for r in new.topics[0].refs["product"]] == ["SRTWC286"]
    assert new.held.domain == "inventory" and [r.key for r in new.held.refs["product"]] == ["SRTWC286"]


# -- S7: new topic about "this one" ---------------------------------------------------------------------- #


def test_s7_new_topic_referring_to_held_carries_that_ref():
    state = mk_state(held("inventory", {"product": ["SRTWC286"]}))
    o = out(ask(act="new_topic", domain="product_attachment", entities=[entity("product", refers_to_held=True)]))
    r = FakeResolver()
    new, actions = apply_turn(state, o, resolver=r)
    assert isinstance(actions[0], Lookup) and actions[0].domain == "product_attachment"
    assert [x.key for x in new.held.refs["product"]] == ["SRTWC286"]
    assert [t.domain for t in new.topics] == ["inventory"]
    assert r.entity_lookups == []


def test_s7_nothing_of_that_kind_held_is_a_miss():
    state = mk_state(held("inventory", {"customer": ["C1"]}))
    o = out(ask(act="new_topic", domain="product_attachment", entities=[entity("product", refers_to_held=True)]))
    new, actions = apply_turn(state, o, resolver=FakeResolver())
    assert isinstance(actions[0], ClarifyAct) and actions[0].reask is True
    assert new.held.domain == "inventory" and "customer" in new.held.refs  # unchanged


# -- S8 / S9: truthful not-found copy ------------------------------------------------------------------------ #


def test_s8_not_found_without_a_phrase_never_prints_the_key():
    state = mk_state(held("sales", {"brand": ["SORENTO"]}))
    o = out(ask(act="refines_held", entities=[entity("product_type", key="water_closet_x")]))
    new, actions = apply_turn(state, o, resolver=FakeResolver())
    a = actions[0]
    assert isinstance(a, ClarifyAct) and a.not_found is True and a.phrase is None
    assert a.pending.asked.strip() and "water_closet_x" not in a.pending.asked
    assert "water_closet_x" not in answer.build_result(new, actions, o, today=TODAY).direct_reply


def test_s9_attachment_type_with_no_options_says_there_are_no_files():
    state = mk_state(held("product_attachment", {"product": ["A"]}))
    o = out(ask(act="refines_held", attachment_type="certification"))
    new, actions = apply_turn(state, o, resolver=FakeResolver())
    a = actions[0]
    assert isinstance(a, ClarifyAct) and a.about == "attachment_type" and a.not_found is True
    assert a.pending.asked == clarify_copy.text("no_files", "en")
    assert new.held.attachment_type is None


# -- S11: the same which_held question repeated is a miss ------------------------------------------------------ #


def test_s11_repeated_which_held_counts_a_miss_then_offers_handover():
    state = mk_state(held("product_attachment", {"product": ["SRTWC286"]}))
    o = out(ask(act="refines_held", entities=[entity("product", phrase="SRTWC8066", op="add")]))
    r = FakeResolver({"entities": {"product|SRTWC8066": {"status": "exact", "refs": [
        {"kind": "product", "key": "SRTWC8066", "label": "SRTWC8066"}]}}})
    s1, a1 = apply_turn(state, o, resolver=r)
    assert a1[0].about == "which_held" and a1[0].reask is False and s1.pending.misses == 0
    s2, a2 = apply_turn(s1, o, resolver=r)
    assert isinstance(a2[0], ClarifyAct) and a2[0].reask is True and s2.pending.misses == 1
    assert [x.label for x in s2.pending.options] == ["SRTWC286", "SRTWC8066"]
    s3, a3 = apply_turn(s2, o, resolver=r)
    assert isinstance(a3[0], OfferHandover)


# -- Re-check blockers (round 2) ------------------------------------------------------------- #


def test_r2_two_different_not_found_codes_are_two_questions_not_a_miss():
    first = out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="SRT1")]))
    second = out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="SRT2")]))
    s1, a1 = apply_turn(mk_state(), first, resolver=FakeResolver(), now=NOW)
    s2, a2 = apply_turn(s1, second, resolver=FakeResolver(), now=NOW)
    assert isinstance(a2[0], ClarifyAct) and not a2[0].reask
    assert "SRT2" in a2[0].pending.asked and "SRT1" not in a2[0].pending.asked
    s3, a3 = apply_turn(s2, out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="SRT3")])),
                        resolver=FakeResolver(), now=NOW)
    assert not isinstance(a3[0], OfferHandover)


def test_r2_unresolved_report_filter_is_said_by_label_never_by_key():
    o = out(ask(act="new_topic", domain="sales", report=_report(filters=[
        Filter(dimension="category", op="in", keys=["wc_one_piece"], values=[])])))
    state, actions = apply_turn(mk_state(), o, resolver=FakeResolver(), now=NOW)
    asked = actions[0].pending.asked
    assert "wc_one_piece" not in asked and "not available yet" in asked
    assert not state.held.report.filters
