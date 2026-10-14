"""PV2 redesign R2, fix round 4 (re-review ruling, plan R2 design item 3, 14 Oct). Red tests on the real `apply_turn`
path with PHRASE-ONLY product parses (`validate()` nulls every product key, so a keyed product is not a real shape).

The FULL origin replays only where held did NOT take the ask (unclear act, flagged clarify, a missed ask's question,
the v1 picker mirror). A question raised after `_commit` replays nothing that already landed: its origin keeps only
the items still waiting on a question. When a turn answers a pending question, the grant checks also judge the held
subject. Pre-R2 expectations come from crew/pv2 941fb867a (exported with git archive into a scratch dir, never a
checkout), rendered once and hard-coded below.

A1 closest pick   A2 remove + ambiguous   A3 which_held brand   A4 spec clear   A5 brand parity (S4 is in fix2)
A6 report pick MEMORY   A7 chained grant re-check   A8 MEMORY per turn   A10 origin not deferred   A11 refused clock
"""
from __future__ import annotations

import re
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE
from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile, render_memory
from app.services.chatbot.pv2.schema import Filter, Period, ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, Option, dump_state, new_state
from tests.chatbot.pv2.carry_fakes import (
    NOW,
    FakeResolver,
    ask,
    entity,
    held as mk_held,
    mk_state,
    out,
    spec,
    spec_lookup_key,
)

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# BEGIN SCENARIOS
class _ClosestResolver(FakeResolver):
    """FakeResolver plus `closest` on a spec result (the closest real products when no axis holds the number)."""

    def resolve_specs(self, specs, *, held):
        res = super().resolve_specs(specs, held=held)
        table = self.config.get("specs", {})
        for s, r in zip(specs, res):
            cfg = table.get(spec_lookup_key(s.attribute, s.value_key, s.number)) or {}
            r.closest = [Option(**o) for o in cfg.get("closest", [])]
        return res


def _exact_ref(kind, key, label=None):
    return {"status": "exact", "refs": [{"kind": kind, "key": key, "label": label or key}]}


def _amb(kind, keys):
    return {"status": "ambiguous", "options": [
        {"key": k, "label": k, "payload": {"kind": kind, "key": k, "label": k}} for k in keys]}


def _pickp(key="1"):
    return out(ask(act="answers_bot_question", answer={"option_key": key, "free_value": None}))


def _scenario_a1():
    closest = [{"key": c, "label": c, "payload": {"kind": "product", "key": c, "label": c}}
               for c in ("SRTWC286-250", "SRTWC286-350")]
    r = _ClosestResolver({"entities": {"product|srtwc286": _exact_ref("product", "SRTWC286")},
                          "specs": {"|300": {"status": "none", "closest": closest}}})
    first = ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="srtwc286")],
                specs=[spec(number=300, unit="mm", phrase="300mm")])
    return r, [out(first), _pickp("SRTWC286-250")]


def _scenario_a2():
    customers = {"status": "ambiguous", "options": [
        {"key": c, "label": n, "payload": {"kind": "customer", "key": c, "label": n}}
        for c, n in (("c1", "ABC SDN BHD"), ("c2", "ABC TRADING"))]}
    r = FakeResolver({"entities": {"product|A1": _exact_ref("product", "A1"), "product|B1": _exact_ref("product", "B1"),
                                   "customer|abc": customers}})
    first = ask(act="new_topic", domain="order", entities=[entity("product", phrase="A1"), entity("product", phrase="B1")])
    drop = ask(act="refines_held", domain="order",
               entities=[entity("product", phrase="A1", op="remove"), entity("customer", phrase="abc")])
    return r, [out(first), out(drop), _pickp("1")]


def _scenario_a3():
    r = FakeResolver({"entities": {"brand|sorento": _exact_ref("brand", "sorento", "Sorento"),
                                   "brand|mocha": _exact_ref("brand", "mocha", "Mocha")},
                      "attachment_types": {"brochure": _exact_ref("attachment_type", "brochure")}})
    first = ask(act="new_topic", domain="product_attachment", attachment_type="brochure",
                entities=[entity("brand", key="sorento", phrase="sorento")])
    more = ask(act="refines_held", domain="product_attachment",
               entities=[entity("brand", key="mocha", phrase="mocha", op="add")])
    return r, [out(first), out(more), _pickp("1")]


def _scenario_a4():
    r = FakeResolver({"entities": {"product|A1": _exact_ref("product", "A1"), "product|mou": _amb("product", ("SRTWC286", "SRTWC287"))},
                      "specs": {"size|600": {"status": "exact", "specs": [{"attribute": "size", "comparator": "eq", "value": "600"}]},
                                "colour|black": {"status": "exact", "specs": [{"attribute": "colour", "comparator": "eq", "value": "black"}]}}})
    first = ask(act="new_topic", domain="incoming", entities=[entity("product", phrase="A1")],
                specs=[spec(attribute="size", value_key="600")])
    black = ask(act="refines_held", domain="incoming", specs=[spec(op="clear"), spec(attribute="colour", value_key="black")],
                entities=[entity("product", phrase="mou")])
    return r, [out(first), out(black), _pickp("1")]


def _play(resolver, steps):
    """[(action class name, state)] and the MEMORY (HELD through TOPICS) after every turn."""
    st, trail, mems = new_state(NOW), [], []
    for parsed in steps:
        st, actions = apply_turn(st, parsed, resolver=resolver, now=NOW)
        trail.append((type(actions[0]).__name__, st))
        mem = render_memory(st, MemoryProfile(audience="staff"), NOW)
        mems.append(mem[mem.index("HELD"):mem.index("LAST ANSWER")])
    return trail, mems
# END SCENARIOS


# ------------------------------------------------------------------ pre-R2 MEMORY after every turn (941fb867a)

PRE_R2_MEMORY = {
    "a1": [
        'HELD: domain=inventory\n  product: SRTWC286\nPENDING QUESTION: kind=clarify_spec_attribute; asked="I couldn\'t find any value named "300mm"."\n  options: 1=SRTWC286-250 (SRTWC286-250) | 2=SRTWC286-350 (SRTWC286-350)\nTOPICS (switch_back keys, newest first): none\n',
        'HELD: domain=inventory\n  product: SRTWC286-250\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
    ],
    "a2": [
        'HELD: domain=order\n  product: A1, B1\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
        'HELD: domain=order\n  product: B1\nPENDING QUESTION: kind=clarify_entity; asked="Which "abc" do you mean?"\n  options: 1=1 (ABC SDN BHD) | 2=2 (ABC TRADING)\nTOPICS (switch_back keys, newest first): none\n',
        'HELD: domain=order\n  customer: ABC SDN BHD\n  product: B1\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
    ],
    "a3": [
        'HELD: domain=product_attachment\n  brand: Sorento\n  attachment_type: brochure\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
        'HELD: domain=product_attachment\n  brand: Sorento\n  attachment_type: brochure\nPENDING QUESTION: kind=clarify_which_held; asked="Which one do you mean?"\n  options: 1=1 (Sorento) | 2=2 (Mocha)\nTOPICS (switch_back keys, newest first): none\n',
        'HELD: domain=product_attachment\n  brand: Sorento\n  attachment_type: brochure\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
    ],
    "a4": [
        'HELD: domain=incoming\n  product: A1\n  spec: size=600\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
        'HELD: domain=incoming\n  product: A1\n  spec: colour=black\nPENDING QUESTION: kind=clarify_entity; asked="Which "mou" do you mean?"\n  options: 1=1 (SRTWC286) | 2=2 (SRTWC287)\nTOPICS (switch_back keys, newest first): none\n',
        'HELD: domain=incoming\n  product: SRTWC286\n  spec: colour=black\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
    ],
}


REAL_PICKS = {"a1": "SRTWC286-250", "a2": "1", "a3": "1", "a4": "1"}


def _refs(st, kind="product"):
    return [r.key for r in st.held.refs.get(kind, [])]


@pytest.mark.parametrize("name", ["a1", "a2", "a3", "a4"])
def test_a8_memory_after_every_turn_equals_pre_r2(name):
    resolver, steps = globals()["_scenario_" + name]()
    _, mems = _play(resolver, steps)
    assert mems == PRE_R2_MEMORY[name], [i for i, (a, b) in enumerate(zip(mems, PRE_R2_MEMORY[name])) if a != b]


# ------------------------------------------------------------------ A1 closest products


def test_a1_a_pick_among_the_closest_products_reaches_a_lookup_on_the_first_pick():
    resolver, steps = _scenario_a1()
    trail, _ = _play(resolver, steps)
    assert trail[0][0] == "ClarifyAct" and trail[0][1].pending.kind == "clarify_spec_attribute"
    act, st = trail[1]
    assert act == "Lookup", "pre-R2 answered on the first pick; replaying the committed 300mm ask asks again"
    assert _refs(st) == ["SRTWC286-250"] and st.pending is None


# ------------------------------------------------------------------ A2 remove + an ambiguous customer


def test_a2_a_pick_after_a_remove_plus_ambiguous_customer_reaches_a_lookup_for_the_rest():
    resolver, steps = _scenario_a2()
    trail, _ = _play(resolver, steps)
    assert trail[1][0] == "ClarifyAct" and _refs(trail[1][1]) == ["B1"]
    act, st = trail[2]
    assert act == "Lookup", "the remove already landed; replaying it is a which_held miss"
    assert _refs(st) == ["B1"] and _refs(st, "customer") == ["c1"]


# ------------------------------------------------------------------ A3 which_held on a brand add


def test_a3_a_pick_on_a_which_held_question_reaches_a_lookup():
    resolver, steps = _scenario_a3()
    trail, _ = _play(resolver, steps)
    assert trail[1][0] == "ClarifyAct" and trail[1][1].pending.kind == "clarify_which_held"
    act, st = trail[2]
    assert act == "Lookup", "a which_held question replays nothing that already landed"
    assert st.pending is None


# ------------------------------------------------------------------ A4 spec clear + a new spec


def test_a4_a_pick_keeps_the_new_spec_a_clearing_refine_set():
    resolver, steps = _scenario_a4()
    trail, _ = _play(resolver, steps)
    assert trail[1][0] == "ClarifyAct" and [(s.attribute, s.value) for s in trail[1][1].held.specs] == [("colour", "black")]
    act, st = trail[2]
    assert act == "Lookup" and _refs(st) == ["SRTWC286"]
    assert [(s.attribute, s.value) for s in st.held.specs] == [("colour", "black")], "pre-R2 kept black"


# ------------------------------------------------------------------ A5 brand parity (pre-R2: the pick replaces the set)


def test_a5_a_brand_added_beside_an_ambiguous_brand_equals_pre_r2_after_the_pick():
    r = FakeResolver({"entities": {"brand|sorento": _exact_ref("brand", "sorento", "Sorento"),
                                   "brand|moc": _amb("brand", ("mocha", "mocca"))}})
    a = ask(act="new_topic", domain="inventory", entities=[entity("brand", key="sorento", phrase="sorento"),
                                                           entity("brand", phrase="moc")])
    st, acts = apply_turn(new_state(NOW), out(a), resolver=r, now=NOW)
    st, acts = apply_turn(st, _pickp("1"), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    assert _refs(st, "brand") == ["mocha"], "pre-R2 (941fb867a) held [mocha]"


# ------------------------------------------------------------------ A6 report pick MEMORY


class _ReportResolver(FakeResolver):
    """Like `DbResolver._report_filter`: the phrase "tan" is ambiguous; an id key is not a name."""

    def resolve_report_spec(self, spec_, *, held, profile=None):
        opts, unresolved = [], []
        for f in spec_.filters:
            for k in f.keys:
                if k == "tan":
                    opts = [{"key": f"cust-{i}", "label": f"TAN {i}",
                             "payload": {"kind": "customer", "key": f"cust-{i}", "label": f"TAN {i}", "dimension": "customer"}}
                            for i in (1, 2)]
                else:
                    unresolved.append(f.dimension)
        status = "ambiguous" if opts else ("none" if unresolved else "exact")
        return SimpleNamespace(status=status, options=opts, refs={}, phrase="tan", unresolved=unresolved)


LM = Period(preset="last_month", n=None, date_from=None, date_to=None)


def _tan_report() -> ReportSpec:
    return ReportSpec(dataset=None, measures=["revenue"], group_by=[], top_n_per_group=None,
                      filters=[Filter(dimension="customer", op="in", keys=["tan"], values=[])], period=LM,
                      compare_to=None, compare_period=None, grain=None, threshold=None, sort=None, top_n=None,
                      basis=None)


def test_a6_memory_after_a_report_pick_equals_pre_r2():
    r = _ReportResolver({})
    st, acts = apply_turn(new_state(NOW), out(ask(act="new_topic", domain="sales", report=_tan_report())), resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct)
    st, acts = apply_turn(st, _pickp("1"), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup) and _refs(st, "customer") == ["cust-1"]
    mem = render_memory(st, MemoryProfile(audience="staff"), NOW)
    line = next(ln for ln in mem.splitlines() if "report:" in ln)
    assert line == ("  report: dataset=none; measures=revenue; basis=none; group_by=none; period=last_month; "
                    "top_n=none; filter=customer in tan"), line
    assert not UUID.search(mem)


# ------------------------------------------------------------------ A7 chained grant re-check

SALES = "sales_orders.sales_report"
PO_GRANT = "purchase_orders.placed"
NAMES = {"customer": ["TAN TRADING 1", "TAN TRADING 2"], "location": ["KL NORTH", "KL SOUTH"]}


class _Progressive(FakeResolver):
    """Like DbResolver: the first ambiguous report filter offers its candidates; an id key resolves exact."""

    def resolve_report_spec(self, spec_, *, held, profile=None):
        refs = {}
        for f in spec_.filters:
            if all(k.startswith("id-") for k in f.keys):
                refs[f.dimension] = [{"kind": f.dimension, "key": k, "label": k} for k in f.keys]
                continue
            opts = [{"key": f"id-{n}", "label": n, "payload": {"kind": f.dimension, "key": f"id-{n}", "label": n,
                                                              "dimension": f.dimension}} for n in NAMES[f.dimension]]
            return SimpleNamespace(status="ambiguous", options=opts, refs=refs, phrase=f.keys[0], unresolved=[])
        return SimpleNamespace(status="exact", options=[], refs=refs, phrase=None, unresolved=[])


def _chain_resolver():
    return _Progressive({"entities": {"product|wc": _amb("product", ("WC-1", "WC-2"))}})


def _run(parsed, raw, *, audience, granted, resolver):
    calls = []
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=raw, profile=MemoryProfile(audience=audience), parse=lambda m, u: parsed,
        resolver=resolver, load_exchanges=lambda s: [], report_answer=lambda *a, **k: calls.append(a),
        granted_reveals=frozenset(granted))
    result = turn._run(ctx)
    from app.services.chatbot.pv2.answer import reply_for

    text = result.direct_reply or "\n".join(filter(None, (reply_for(a, "en") for a in result.actions)))
    return result, text, calls


def test_a7_a_chained_report_pick_is_refused_once_the_grant_is_revoked_mid_chain():
    rs = ReportSpec(dataset=None, measures=["revenue"], group_by=[], top_n_per_group=None,
                    filters=[Filter(dimension="location", op="in", keys=["location"], values=[]),
                             Filter(dimension="customer", op="in", keys=["customer"], values=[])],
                    period=LM, compare_to=None, compare_period=None, grain=None, threshold=None, sort=None,
                    top_n=None, basis=None)
    first = out(ask(act="new_topic", domain=None, report=rs, period=LM, entities=[entity("product", phrase="wc")]))
    pick = _pickp("1")
    r1, t1, _ = _run(first, dump_state(new_state(NOW)), audience="staff", granted=[SALES], resolver=_chain_resolver())
    assert "WC-1" in t1
    r2, t2, _ = _run(pick, dump_state(r1.state), audience="staff", granted=[SALES], resolver=_chain_resolver())
    assert "KL NORTH" in t2, t2
    r3, t3, calls = _run(pick, dump_state(r2.state), audience="staff", granted=[], resolver=_chain_resolver())
    assert r3.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE, t3
    assert not any(n in t3 for n in ("TAN TRADING", "KL ", "WC-")) and calls == []


def test_a7_a_chained_po_pick_is_refused_once_the_grant_is_revoked_mid_chain():
    cfg = {"entities": {"product|mou": _amb("product", ("MOU-1", "MOU-2")),
                        "supplier|abc": _amb("supplier", ("ABC CERAMICS CO", "ABC FAUCET LTD"))}}
    first = out(ask(act="new_topic", domain="purchase_order",
                    entities=[entity("product", phrase="mou"), entity("supplier", phrase="abc")]))
    pick = _pickp("1")
    r1, t1, _ = _run(first, dump_state(new_state(NOW)), audience="staff", granted=[PO_GRANT], resolver=FakeResolver(cfg))
    assert "MOU-1" in t1
    r2, t2, _ = _run(pick, dump_state(r1.state), audience="staff", granted=[PO_GRANT], resolver=FakeResolver(cfg))
    assert "ABC CERAMICS" in t2, t2
    r3, t3, _ = _run(pick, dump_state(r2.state), audience="staff", granted=[], resolver=FakeResolver(cfg))
    assert r3.verdict is None and r3.direct_reply
    assert not any(n in t3 for n in ("ABC ", "MOU-")), t3
    assert "purchase order" in r3.direct_reply.lower()


# ------------------------------------------------------------------ A10 a NEW pending carries origin, not deferred


def _first_turn_pending(parsed, resolver, held=None):
    st = mk_state(held) if held is not None else new_state(NOW)
    st, acts = apply_turn(st, parsed, resolver=resolver, now=NOW)
    assert isinstance(acts[0], ClarifyAct)
    return st.pending


def test_a10_an_entity_clarify_carries_origin_and_no_deferred():
    p = _first_turn_pending(out(ask(act="new_topic", domain="incoming", period=LM,
                                    entities=[entity("product", phrase="mou")])),
                            FakeResolver({"entities": {"product|mou": _amb("product", ("SRTWC286", "SRTWC287"))}}))
    assert p.origin is not None and p.deferred is None


def test_a10_a_flagged_clarify_carries_origin_and_no_deferred():
    p = _first_turn_pending(
        out(ask(act="unclear", domain="incoming"), clarify={"needed": True, "about": "entity", "phrase": "mou"}),
        FakeResolver({}), held=mk_held("inventory", {"product": ["SRTWC286"]}))
    assert p.origin is not None and p.deferred is None


def test_a10_a_missed_asks_question_carries_origin_and_no_deferred():
    p = _first_turn_pending(out(ask(act="refines_held", domain="incoming", period=LM)), FakeResolver({}))
    assert p.origin is not None and p.deferred is None


# ------------------------------------------------------------------ A11 a refused turn still advances the clock


def _refuse_at(now, raw):
    calls = []
    ctx = turn.TurnV2Context(
        text="x", now=now, raw_state=raw, profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: out(ask(act="new_topic", domain=None, report=_tan_report())),
        resolver=_ReportResolver({}), load_exchanges=lambda s: [], report_answer=lambda *a, **k: calls.append(a),
        granted_reveals=frozenset())
    return turn._run(ctx)


def test_a11_a_refused_turn_advances_the_session_clock_and_keeps_everything_else():
    """Pre-R2: the post-lookup grant refusal returned the turn's own state (clock advanced); the pre-lookup refusal
    returned state_in untouched. Only the former path is pinned: nothing else may change, but repeated refusals must
    not let the session roll."""
    before = mk_state(mk_held("inventory", {"product": ["A1"]}), last_turn_at=NOW - timedelta(hours=3))
    first = _refuse_at(NOW, dump_state(before))
    assert first.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE
    assert first.state.last_turn_at == NOW
    assert (first.state.held, first.state.pending, first.state.topics) == (before.held, before.pending, before.topics)
    second = _refuse_at(NOW + timedelta(hours=3), dump_state(first.state))
    assert second.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE
    assert second.state.held == before.held and second.state.topics == [], "the session did not roll"
