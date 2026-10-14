"""PV2 redesign R2, fix round 2 (code review of a28a30f2b). Red tests, no model call.

Every test drives the REAL path: `apply_turn` asks a question from a handwritten parse, then the user picks (and picks
again). Pre-R2 behaviour (crew/pv2 941fb867a) is the reference for M2, M3 and S4.

M1 chained picks end (a guard on f89d32511: already green)   M2 no duplicate topics (same subject, different sids)
M3 a pick keeps the spec / attachment kind the original ask set   S4 each picked product keeps its own quantity
S5 the register names the printing subject of EACH envelope   S6 branch guards (one test per mutation)
N1 a pre-R2 held answered through a v1 pick / free value / "all" gets a real sid
"""
from __future__ import annotations

from tests.chatbot.pv2 import _register_envelopes as E  # loads the worktree MCP first

import pytest

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile, render_memory
from app.services.chatbot.pv2.schema import Ask, Quantity
from app.services.chatbot.pv2.state import (
    ClarifyAct,
    Lookup,
    Option,
    PendingQuestion,
    new_state,
    subject_label,
)
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, out, spec


def _pick(key="1"):
    return out(ask(act="answers_bot_question", answer={"option_key": key, "free_value": None}))


def _opts(kind, keys):
    return [{"key": k, "label": k, "payload": {"kind": kind, "key": k, "label": k}} for k in keys]


MOU = {"status": "ambiguous", "options": _opts("product", ("SRTWC286", "SRTWC287"))}
EXACT = {f"product|{k}": {"status": "exact", "refs": [{"kind": "product", "key": k, "label": k}]}
         for k in ("A1", "B1", "C1")}
ATTACH = {k: {"status": "exact", "refs": [{"kind": "attachment_type", "key": k, "label": k}]}
          for k in ("datasheet", "brochure")}


def _spec_exact(value):
    return {"status": "exact", "specs": [{"attribute": "colour", "comparator": "eq", "value": value}]}


def _keys(state, kind="product"):
    return [r.key for r in state.held.refs.get(kind, [])]


def _qty(state):
    return {r.key: (r.quantity.number if r.quantity else None) for r in state.held.refs.get("product", [])}


# ------------------------------------------------------------------ M1 chained picks end (guards: green on f89d32511)


def test_m1_two_ambiguous_items_reach_a_lookup_after_exactly_two_picks_with_both_picks():
    customers = {"status": "ambiguous", "options": [
        {"key": c, "label": n, "payload": {"kind": "customer", "key": c, "label": n}}
        for c, n in (("c1", "ABC SDN BHD"), ("c2", "ABC TRADING"))]}
    r = FakeResolver({"entities": {"product|mou": MOU, "customer|abc": customers}})
    a = ask(act="new_topic", domain="order", entities=[entity("product", phrase="mou"), entity("customer", phrase="abc")])
    st, acts = apply_turn(new_state(NOW), out(a), resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct)
    st, acts = apply_turn(st, _pick(), resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct), "one item is still ambiguous after the first pick"
    st, acts = apply_turn(st, _pick(), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    assert _keys(st) == ["SRTWC286"] and _keys(st, "customer") == ["c1"] and st.held.domain == "order"


def test_m1_an_ambiguous_product_and_an_ambiguous_spec_reach_their_lookup_with_both_picks():
    sopts = [{"key": v, "label": v, "payload": {"kind": "spec", "attribute": "colour", "comparator": "eq", "value": v}}
             for v in ("black", "grey")]
    r = FakeResolver({"entities": {"product|mou": MOU}, "specs": {"colour|blk": {"status": "ambiguous", "options": sopts}}})
    a = ask(act="new_topic", domain="incoming", entities=[entity("product", phrase="mou")],
            specs=[spec(attribute="colour", value_key="blk")])
    st, acts = apply_turn(new_state(NOW), out(a), resolver=r, now=NOW)
    for _ in range(3):
        if isinstance(acts[0], Lookup):
            break
        key = "1" if st.pending.kind == "clarify_entity" else st.pending.options[0].key
        st, acts = apply_turn(st, _pick(key), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    assert _keys(st) == ["SRTWC286"] and [s.value for s in st.held.specs] == ["black"]


# ------------------------------------------------------------------ M2 no duplicate topics


def _topics_block(state) -> str:
    mem = render_memory(state, MemoryProfile(audience="staff"), NOW)
    return mem[mem.index("TOPICS"):mem.index("LAST ANSWER")]


def test_m2_the_same_subject_pushed_twice_is_one_topic_and_memory_equals_pre_r2():
    r = FakeResolver({"entities": EXACT})

    def nt(k):
        return out(ask(act="new_topic", domain="inventory", entities=[entity("product", key=k)]))

    st = new_state(NOW)
    for k in ("A1", "B1", "A1", "C1"):
        st, _ = apply_turn(st, nt(k), resolver=r, now=NOW)
    labels = [[x.key for x in t.refs["product"]] for t in st.topics]
    assert labels == [["A1"], ["B1"]], f"pre-R2 kept T1 A1, T2 B1; got {labels}"
    # Pre-R2 MEMORY for the same turns: a state with no sids and the two distinct topics.
    pre_r2 = mk_state(mk_held("inventory", {"product": ["C1"]}),
                      topics=[mk_held("inventory", {"product": ["A1"]}), mk_held("inventory", {"product": ["B1"]})])
    assert _topics_block(st) == _topics_block(pre_r2)


# ------------------------------------------------------------------ M3 a pick keeps the spec / attachment kind the ask set


def test_m3_a_pick_keeps_the_attachment_kind_a_clearing_refine_set():
    r = FakeResolver({"entities": {**EXACT, "product|mou": MOU}, "attachment_types": ATTACH})
    first = ask(act="new_topic", domain="resource", attachment_type="brochure", entities=[entity("product", key="A1")])
    st, acts = apply_turn(new_state(NOW), out(first), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup) and st.held.attachment_type == "brochure"
    refine = ask(act="refines_held", domain="resource", cleared=["attachment_type"], attachment_type="datasheet",
                 entities=[entity("product", phrase="mou")])
    st, acts = apply_turn(st, out(refine), resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct) and st.pending.kind == "clarify_entity"
    st, acts = apply_turn(st, _pick(), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    assert _keys(st) == ["SRTWC286"]
    assert st.held.attachment_type == "datasheet", "pre-R2 kept the datasheet the refine asked for"


def test_m3_a_pick_keeps_the_spec_a_clearing_refine_set():
    r = FakeResolver({"entities": {**EXACT, "product|mou": MOU},
                      "specs": {"colour|grey": _spec_exact("grey"), "colour|black": _spec_exact("black")}})
    first = ask(act="new_topic", domain="incoming", specs=[spec(attribute="colour", value_key="grey")],
                entities=[entity("product", key="A1")])
    st, acts = apply_turn(new_state(NOW), out(first), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup) and [s.value for s in st.held.specs] == ["grey"]
    refine = ask(act="refines_held", domain="incoming", cleared=["specs"],
                 specs=[spec(attribute="colour", value_key="black")], entities=[entity("product", phrase="mou")])
    st, acts = apply_turn(st, out(refine), resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct) and st.pending.kind == "clarify_entity"
    st, acts = apply_turn(st, _pick(), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    assert [s.value for s in st.held.specs] == ["black"], "pre-R2 kept the black the refine asked for"


# ------------------------------------------------------------------ S4 each picked product keeps its own quantity


def _qty_ask(*ents):
    return out(ask(act="new_topic", domain="incoming", entities=list(ents)))


def _with_qty(e, n):
    return e.model_copy(update={"quantity": Quantity(number=n, unit=None)})


def test_s4_the_picked_product_takes_its_own_quantity_not_the_siblings():
    r = FakeResolver({"entities": {**EXACT, "product|mou": MOU}})
    parsed = _qty_ask(_with_qty(entity("product", phrase="A1"), 5), _with_qty(entity("product", phrase="mou"), 3))
    st, acts = apply_turn(new_state(NOW), parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct)
    st, acts = apply_turn(st, _pick(), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    assert _qty(st) == {"SRTWC286": 3}, "pre-R2 (941fb867a, phrase-only parse): the pick carried mou's own 3"


def test_s4_a_picked_product_with_no_quantity_does_not_inherit_the_siblings():
    r = FakeResolver({"entities": {**EXACT, "product|mou": MOU}})
    parsed = _qty_ask(_with_qty(entity("product", phrase="A1"), 5), entity("product", phrase="mou"))
    st, acts = apply_turn(new_state(NOW), parsed, resolver=r, now=NOW)
    st, acts = apply_turn(st, _pick(), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    assert _qty(st) == {"SRTWC286": None}, "pre-R2 (941fb867a, phrase-only parse) gave the picked product no quantity"


# ------------------------------------------------------------------ S5 the register names each envelope's own subject


def test_s5_a_multi_ask_turn_records_each_envelope_under_its_own_subject():
    from app.services.chatbot import engine as engine_mod

    r = FakeResolver({"entities": EXACT})
    two = out(ask(act="new_topic", domain="inventory", entities=[entity("product", key="A1")]),
              ask(act="new_topic", domain="incoming", entities=[entity("product", key="B1")]))
    st, acts = apply_turn(new_state(NOW), two, resolver=r, now=NOW)
    assert [type(a) for a in acts] == [Lookup, Lookup]
    assert st.held.domain == "incoming" and st.topics[0].domain == "inventory"
    stock = E.envelope(E.STOCK_TOOL, E.STOCK_RAW, domain="inventory")
    incoming = E.envelope(E.INCOMING_TOOL, E.INCOMING_RAW, domain="incoming")
    engine_mod._pv2_write_register({"pv2_state": st}, [stock, incoming])
    by_label = {e.label: subject_label(st, e.subject) for e in st.register}
    assert by_label["SRTBF11201-NEW"] == "S2", by_label  # the inventory ask's subject (A1), now T1 / S2
    assert by_label["SRTWC286-SH-NEW"] == "S1", by_label  # the incoming ask's subject (B1), the held one


# ------------------------------------------------------------------ S6 branch guards (each goes red under its mutation)


def test_s6a_a_question_raised_while_answering_a_report_question_keeps_the_original_ask():
    """Guards `_keep_origin`'s answered branch: a report measure pick that raises the period question (the pick has
    no origin-settled ask to carry, `settled` is None) leaves that question with the ORIGINAL ask, not the pick."""
    from app.services.chatbot.pv2.schema import ReportSpec

    rs = ReportSpec(dataset="sales", measures=[], group_by=["customer"], top_n_per_group=None, filters=[],
                    period=None, compare_to=None, compare_period=None, grain=None, threshold=None, sort=None,
                    top_n=5, basis=None)
    a = ask(act="new_topic", domain="sales", report=rs)
    r = FakeResolver({})
    st, acts = apply_turn(new_state(NOW), out(a), resolver=r, now=NOW)
    assert st.pending.kind == "clarify_report_measure"
    st, acts = apply_turn(st, _pick(st.pending.options[0].key), resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct) and st.pending.kind == "clarify_report_period"
    assert Ask.model_validate(st.pending.origin) == a, "the second question still completes the ORIGINAL ask"


def test_s6b_last_lookup_ask_after_a_pick_is_the_original_ask():
    """Guards `_last_lookup_ask`'s use of the answered question's origin."""
    r = FakeResolver({"entities": {"product|mou": MOU}})
    a = ask(act="new_topic", domain="incoming", entities=[entity("product", phrase="mou")])
    st, _ = apply_turn(new_state(NOW), out(a), resolver=r, now=NOW)
    st, acts = apply_turn(st, _pick(), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    assert Ask.model_validate(st.last_lookup_ask) == a, "the Lookup answered the original ask, not the pick"


def test_s6c_a_domain_switch_refine_does_not_leave_held_and_topics_with_the_same_sid():
    """Guards `_commit`'s collision check: the refine pushes the old self to topics, so it needs a fresh sid."""
    r = FakeResolver({"entities": EXACT})
    st, _ = apply_turn(new_state(NOW), out(ask(act="new_topic", domain="inventory", entities=[entity("product", key="A1")])),
                       resolver=r, now=NOW)
    old = st.held.sid
    st, acts = apply_turn(st, out(ask(act="refines_held", domain="incoming")), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup) and st.held.domain == "incoming"
    assert [t.sid for t in st.topics] == [old], "the previous self sits in topics under its own sid"
    assert st.held.sid and st.held.sid != old
    assert subject_label(st, old) == "S2" and subject_label(st, st.held.sid) == "S1"


# ------------------------------------------------------------------ N1 a pre-R2 held answered without _commit gets a real sid


def _v1_pending():
    return PendingQuestion(kind="v1_open_question", asked="Which product?", asked_turn=3, v1_kind="product_pick",
                           options=[Option(key="1", label="A1-V1", payload={"kind": "choice", "key": "1", "v1_kind": "product_pick"})])


def _assert_real_sid(after):
    assert after.held.sid, "the subject has a sid"
    assert subject_label(after, after.held.sid) == "S1"


def test_n1_a_pre_r2_held_answered_through_a_v1_pick_gets_a_real_sid():
    st = mk_state(mk_held("incoming", {"product": ["A1"]}), pending_=_v1_pending())  # no sid: saved before R2
    assert st.held.sid is None
    after, acts = apply_turn(st, _pick(), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup)
    _assert_real_sid(after)


def test_n1_a_pre_r2_held_answered_with_a_free_value_gets_a_real_sid():
    st = mk_state(mk_held("incoming", {"product": ["A1"]}), pending_=_v1_pending())
    free = out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": "the blue one"}))
    after, acts = apply_turn(st, free, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup)
    _assert_real_sid(after)


def test_n1_the_all_pick_on_an_empty_held_gets_a_real_sid():
    pending = PendingQuestion(
        kind="clarify_entity", asked='Which "mou" do you mean?', asked_turn=3,
        options=[Option(key=str(n), label=k, payload={"key": k, "kind": "product", "label": k, "op": "set"})
                 for n, k in enumerate(("SRTWC286", "SRTWC287"), 1)])
    st = mk_state(pending_=pending)
    after, acts = apply_turn(st, _pick("all"), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup) and _keys(after) == ["SRTWC286", "SRTWC287"]
    _assert_real_sid(after)
