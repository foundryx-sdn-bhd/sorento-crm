"""PV2 redesign R2 (UAC R2-1..R2-11): the subject stack, `PendingQuestion.origin`, and the R1 carry-overs.

Pinned names (the plan's, stated by the tester): `Held.sid: str | None` ("s<n>", from `ConversationState.next_sid`),
`pv2.state.subject_label(state, sid) -> "S1".."S5" | None`, `pv2.state.subject_at(state, "S<n>") -> Held | None`,
`PendingQuestion.origin: dict | None` (an `Ask` model dump). `RegisterEntry.subject` now holds the sid ("s<n>").

No model call, no parser call: `apply_turn` with the fake resolver, `build_result` with a fake report runner, the real
presenter / structurer for envelopes, and the real engine with the PV2 turn module faked (as the R1 engine tests do).
Names that do not exist yet fail with AttributeError / ImportError; behaviour not built yet fails on its assertion.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.services.chatbot.pv2 import answer, register, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile, render_memory
from app.services.chatbot.pv2.schema import Ask, Period, ParserV2Output, ReportSpec
from app.services.chatbot.pv2.state import (
    ClarifyAct,
    ConversationState,
    Held,
    Lookup,
    Option,
    PendingQuestion,
    RegisterEntry,
    dump_state,
    load_state,
    new_state,
    push_topic,
)
from tests.chatbot.pv2 import _register_envelopes as E
from tests.chatbot.pv2 import replay
from tests.chatbot.pv2.carry_fakes import (
    NOW,
    FakeResolution,
    FakeResolver,
    ask,
    entity,
    held as mk_held,
    mk_state,
    option,
    out,
    spec,
)

TODAY = date(2026, 10, 14)
LAST_MONTH = Period(preset="last_month", n=None, date_from=None, date_to=None)


def _state_with(held_: Held, *, topics: list[Held] | None = None, next_sid: int | None = None, **kw) -> ConversationState:
    st = mk_state(held_, topics=topics, **kw)
    if next_sid is not None:
        st.next_sid = next_sid  # R2 field: AttributeError until it exists
    return st


def _exact(*keys: str) -> dict:
    return {"status": "exact", "refs": [{"kind": "product", "key": k, "label": k} for k in keys]}


PRODUCTS = {f"product|{k}": _exact(k) for k in ("A1", "B1", "C1", "SRTWC286")}


def _new_topic(key: str):
    return out(ask(act="new_topic", domain="inventory", entities=[entity("product", key=key)]))


# ------------------------------------------------------------------ R2-1 stable sid


def test_r2_1_the_first_subject_of_a_fresh_state_gets_a_sid():
    st, actions = apply_turn(new_state(NOW), _new_topic("A1"), resolver=FakeResolver({"entities": PRODUCTS}), now=NOW)
    assert isinstance(actions[0], Lookup)
    assert isinstance(st.held.sid, str) and st.held.sid
    assert isinstance(st.next_sid, int)


def test_r2_1_a_new_topic_gets_a_new_sid_and_the_old_subject_keeps_its_own_in_topics():
    r = FakeResolver({"entities": PRODUCTS})
    st1, _ = apply_turn(new_state(NOW), _new_topic("A1"), resolver=r, now=NOW)
    first = st1.held.sid
    st2, _ = apply_turn(st1, _new_topic("B1"), resolver=r, now=NOW)
    assert st2.held.sid and st2.held.sid != first
    assert [t.sid for t in st2.topics] == [first]
    st3, _ = apply_turn(st2, _new_topic("C1"), resolver=r, now=NOW)
    assert len({st3.held.sid, *[t.sid for t in st3.topics]}) == 3, "every subject has its own sid"
    assert [t.sid for t in st3.topics] == [st2.held.sid, first], "newest first, each sid unchanged"


def test_r2_1_switching_back_returns_the_subject_with_its_original_sid():
    r = FakeResolver({"entities": PRODUCTS})
    st1, _ = apply_turn(new_state(NOW), _new_topic("A1"), resolver=r, now=NOW)
    first = st1.held.sid
    st2, _ = apply_turn(st1, _new_topic("B1"), resolver=r, now=NOW)
    second = st2.held.sid
    back = out(ask(act="switch_back", switch_back_to="T1"))
    st3, _ = apply_turn(st2, back, resolver=r, now=NOW)
    assert st3.held.sid == first, "the switched-back subject keeps its sid"
    assert [t.sid for t in st3.topics] == [second], "the subject switched away from keeps its sid too"


def test_r2_1_a_refine_keeps_the_held_subjects_sid():
    r = FakeResolver({"entities": PRODUCTS})
    st1, _ = apply_turn(new_state(NOW), _new_topic("A1"), resolver=r, now=NOW)
    refine = out(ask(act="refines_held", domain="inventory", period=LAST_MONTH))
    st2, _ = apply_turn(st1, refine, resolver=r, now=NOW)
    assert st1.held.sid is not None and st2.held.sid == st1.held.sid


def test_r2_1_the_session_roll_keeps_the_sid_of_the_subject_it_moves_to_topics():
    from datetime import timedelta

    from app.services.chatbot.pv2.state import roll_session

    st = _state_with(mk_held("inventory", {"product": ["A1"]}, sid="s1"), next_sid=2)
    rolled = roll_session(st, st.last_turn_at + timedelta(hours=5))
    assert [t.sid for t in rolled.topics] == ["s1"]


# ------------------------------------------------------------------ R2-2 / R2-3 labels and lookups


def test_r2_2_a_register_entry_names_its_subject_by_sid_and_the_label_follows_the_stack():
    from app.services.chatbot.pv2.state import subject_label

    st = _state_with(mk_held("inventory", {"product": ["A1"]}, sid="s1"), next_sid=2)
    st.register = [RegisterEntry(id="R1", kind="product", key="A1", label="A1", parent=None, subject="s1", turn=3)]
    assert subject_label(st, st.register[0].subject) == "S1"
    after, _ = apply_turn(st, _new_topic("B1"), resolver=FakeResolver({"entities": PRODUCTS}), now=NOW)
    assert subject_label(after, "s1") == "S2", "a new topic pushes the printing subject to S2"
    assert subject_label(after, after.held.sid) == "S1"
    assert subject_label(after, "s99") is None


def test_r2_2_the_label_runs_s1_to_s5_with_the_topic_cap():
    from app.services.chatbot.pv2.state import subject_label

    topics = [mk_held("inventory", {"product": [f"P{n}"]}, sid=f"s{n}") for n in range(5, 1, -1)]  # s5..s2
    st = _state_with(mk_held("inventory", {"product": ["P6"]}, sid="s6"), topics=topics, next_sid=7)
    assert [subject_label(st, s) for s in ("s6", "s5", "s4", "s3", "s2")] == ["S1", "S2", "S3", "S4", "S5"]
    assert subject_label(st, "s1") is None
    assert subject_label(st, None) is None


def test_r2_3_subject_at_returns_the_subject_and_unknown_is_none():
    from app.services.chatbot.pv2.state import subject_at

    topics = [mk_held("inventory", {"product": ["P2"]}, sid="s2"), mk_held("order", {"product": ["P1"]}, sid="s1")]
    st = _state_with(mk_held("inventory", {"product": ["P3"]}, sid="s3"), topics=topics, next_sid=4)
    assert subject_at(st, "S1").sid == "s3"
    assert subject_at(st, "S2").sid == "s2"
    assert subject_at(st, "S3").sid == "s1"
    for bad in ("S4", "S6", "S0", "S9", "s1", "T1", "R1", "", "S", "S-1", None):
        assert subject_at(st, bad) is None, bad


def test_r2_3_subject_at_and_label_are_inverse():
    from app.services.chatbot.pv2.state import subject_at, subject_label

    topics = [mk_held("inventory", {"product": ["P2"]}, sid="s2")]
    st = _state_with(mk_held("inventory", {"product": ["P3"]}, sid="s3"), topics=topics, next_sid=4)
    for sid in ("s3", "s2"):
        assert subject_at(st, subject_label(st, sid)).sid == sid


# ------------------------------------------------------------------ R2-4 MEMORY unchanged


def test_r2_4_memory_is_byte_identical_with_and_without_sids_and_origin():
    plain = mk_state(mk_held("inventory", {"product": ["SRTWC286"]}),
                     topics=[mk_held("order", {"product": ["A1"]})],
                     pending_=PendingQuestion(kind="clarify_entity", asked="Which one?", asked_turn=3,
                                              options=[option("1", "A1"), option("2", "B1")]))
    decorated = plain.model_copy(deep=True)
    decorated.held.sid = "s2"
    decorated.topics[0].sid = "s1"
    decorated.next_sid = 3
    decorated.pending.origin = ask(act="new_topic", domain="inventory", period=LAST_MONTH).model_dump(mode="json")
    assert decorated.held.sid == "s2" and decorated.pending.origin["domain"] == "inventory"
    profile = MemoryProfile(audience="staff")
    memory = render_memory(decorated, profile, NOW)
    assert memory == render_memory(plain, profile, NOW)
    assert "s1" not in memory.split("TOPICS")[1] and "S1" not in memory and "S2" not in memory


# ------------------------------------------------------------------ R2-5 origin on every interrupting question


def _dym_resolver() -> FakeResolver:
    class R(FakeResolver):
        @staticmethod
        def _build(cfg, phrase):
            res = FakeResolver._build(cfg, phrase)
            res.did_you_mean = bool((cfg or {}).get("did_you_mean"))
            return res

    opts = [{"key": c, "label": c, "payload": {"kind": "product", "key": c, "label": c}} for c in ("SRTWC286", "SRTWC287")]
    return R({"entities": {"product|mou": {"status": "ambiguous", "options": opts, "did_you_mean": True}}})


def _ambiguous_resolver() -> FakeResolver:
    opts = [{"key": c, "label": c, "payload": {"kind": "product", "key": c, "label": c}} for c in ("SRTWC286", "SRTWC287")]
    return FakeResolver({"entities": {"product|mou": {"status": "ambiguous", "options": opts}}})


def _origin_ask():
    return ask(act="new_topic", domain="incoming", period=LAST_MONTH, attachment_type=["datasheet"],
               entities=[entity("product", phrase="mou")])


def _origin_of(pending: PendingQuestion) -> Ask:
    assert pending.origin is not None, "the question interrupted an ask and must keep it"
    return Ask.model_validate(pending.origin)


def test_r2_5_an_entity_clarify_keeps_the_original_ask():
    a = _origin_ask()
    st, actions = apply_turn(mk_state(), out(a), resolver=_ambiguous_resolver(), now=NOW)
    assert isinstance(actions[0], ClarifyAct) and actions[0].pending.kind == "clarify_entity"
    assert _origin_of(st.pending) == a
    assert _origin_of(actions[0].pending) == a


def test_r2_5_a_did_you_mean_keeps_the_original_ask():
    a = _origin_ask()
    st, actions = apply_turn(mk_state(), out(a), resolver=_dym_resolver(), now=NOW)
    assert isinstance(actions[0], ClarifyAct) and actions[0].pending.kind == "clarify_did_you_mean"
    assert _origin_of(st.pending) == a


def test_r2_5_a_flagged_clarify_keeps_the_original_ask():
    a = ask(act="unclear", domain="incoming")
    st = mk_state(mk_held("inventory", {"product": ["SRTWC286"]}))
    after, actions = apply_turn(
        st, out(a, clarify={"needed": True, "about": "entity", "phrase": "mou"}), resolver=FakeResolver({}), now=NOW)
    assert isinstance(actions[0], ClarifyAct)
    assert _origin_of(after.pending) == a


def test_r2_5_a_missed_asks_question_keeps_the_original_ask():
    a = ask(act="refines_held", period=LAST_MONTH)  # nothing held: a which_held question
    after, actions = apply_turn(mk_state(), out(a), resolver=FakeResolver({}), now=NOW)
    assert isinstance(actions[0], ClarifyAct) and actions[0].pending.kind == "clarify_which_held"
    assert _origin_of(after.pending) == a


def test_r2_5_a_re_asked_question_keeps_the_origin_it_had():
    a = _origin_ask()
    first, _ = apply_turn(mk_state(), out(a), resolver=_ambiguous_resolver(), now=NOW)
    garbled = out(ask(act="unclear"))
    again, actions = apply_turn(first, garbled, resolver=FakeResolver({}), now=NOW)
    assert isinstance(actions[0], ClarifyAct) and actions[0].reask
    assert _origin_of(again.pending) == a


def test_r2_5_the_v1_picker_mirrored_after_a_pv2_lookup_keeps_the_ask_that_lookup_answered():
    """`ConversationState.last_lookup_ask` (an Ask dump) is set when a PV2 turn ends in a Lookup bridged to v1 and
    cleared on a turn that does not; `mirror_v1_pending` builds `PendingQuestion.origin` from it."""
    from app.services.chatbot.turn import pending as v1p

    a = ask(act="new_topic", domain="incoming", period=LAST_MONTH, entities=[entity("product", key="SRTWC286")])
    r = FakeResolver({"entities": PRODUCTS})
    after, actions = apply_turn(new_state(NOW), out(a), resolver=r, now=NOW)
    assert isinstance(actions[0], Lookup)
    assert Ask.model_validate(after.last_lookup_ask) == a
    opts = [{"code": f"C{i}", "label": f"SRTWC286-V{i}", "position": i, "entity_type": "product"} for i in (1, 2, 3)]
    v1 = v1p.ask("product_pick", opts, team="purchasing", expects=None, payload={"domain": "incoming"}, asked_at_turn=3)
    mirrored = turn.mirror_v1_pending(after, v1, last_reply="Which product do you mean? Please choose:\n1. SRTWC286-V1")
    assert mirrored.pending is not None and mirrored.pending.kind == "v1_open_question"
    origin = _origin_of(mirrored.pending)
    assert origin == a and origin.domain == "incoming" and origin.period == LAST_MONTH


def test_r2_5_last_lookup_ask_is_cleared_by_a_turn_that_is_not_a_lookup():
    a = ask(act="new_topic", domain="incoming", entities=[entity("product", key="SRTWC286")])
    after, _ = apply_turn(new_state(NOW), out(a), resolver=FakeResolver({"entities": PRODUCTS}), now=NOW)
    assert after.last_lookup_ask is not None
    nxt, actions = apply_turn(after, out(ask(act="small_talk")), resolver=FakeResolver({}), now=NOW)
    assert not isinstance(actions[0], Lookup)
    assert nxt.last_lookup_ask is None


# ------------------------------------------------------------------ R2-6 a pick completes the ORIGINAL ask


def _pick(key: str = "1"):
    return out(ask(act="answers_bot_question", answer={"option_key": key, "free_value": None}))


def _entity_pending(origin: Ask, *, held_key: str = "SRTWC286") -> PendingQuestion:
    return PendingQuestion(
        kind="clarify_entity", asked='Which "mou" do you mean?', asked_turn=3,
        options=[Option(key="1", label=held_key, payload={"key": held_key, "kind": "product", "label": held_key, "op": "set"})],
        origin=origin.model_dump(mode="json"))


def _picked(origin: Ask, held_: Held | None = None, resolver=None):
    st = mk_state(held_ or mk_held("inventory", {"product": ["OTHER1"]}), pending_=_entity_pending(origin))
    after, actions = apply_turn(st, _pick(), resolver=resolver or FakeResolver({}), now=NOW)
    assert isinstance(actions[0], Lookup), actions
    return after, actions[0]


def test_r2_6_any_eta_mou_then_1_answers_incoming_for_the_picked_product():
    after, lookup = _picked(ask(act="unclear", domain="incoming"))
    assert after.held.domain == "incoming", "1 must answer the ETA that was asked, not re-run stock"
    assert [r.key for r in after.held.refs["product"]] == ["SRTWC286"]
    assert lookup.domain == "incoming" and lookup.held.domain == "incoming"


def test_r2_6_the_full_owner_flow_question_then_pick_from_the_recorded_turn():
    rows = replay.load_turns(replay.FIXTURE_DIR / "livecheck_r7_r8.jsonl")
    first = next(r for r in rows if r["case"] == "r8-2-eta-mou-question")
    pick = dict(next(r for r in rows if r["case"] == "r8-2-eta-mou-pick-1"), turn=2, state_in=None)
    results = replay.run_case([first, pick])
    assert _origin_of(results[0].state_out.pending).domain == "incoming"
    held = results[1].state_out.held
    assert held.domain == "incoming" and [r.key for r in held.refs["product"]] == ["SRTWC286"]


def test_r2_6_the_original_period_survives_the_pick():
    after, _ = _picked(ask(act="new_topic", domain="inventory", period=LAST_MONTH))
    assert after.held.period == LAST_MONTH
    assert [r.key for r in after.held.refs["product"]] == ["SRTWC286"]


def test_r2_6_the_original_attachment_kinds_survive_the_pick():
    resolver = FakeResolver({"attachment_types": {
        k: {"status": "exact", "refs": [{"kind": "attachment_type", "key": k, "label": k}]}
        for k in ("datasheet", "brochure")}})
    after, _ = _picked(ask(act="new_topic", domain="resource", attachment_type=["datasheet", "brochure"]),
                       resolver=resolver)
    assert after.held.attachment_type == "datasheet"
    assert after.held.attachment_types == ["datasheet", "brochure"]
    assert after.held.domain == "resource"


def test_r2_6_the_original_status_survives_the_pick():
    after, _ = _picked(ask(act="new_topic", domain="order", status="outstanding"))
    assert after.held.status == "outstanding" and after.held.domain == "order"


def test_r2_6_the_original_spec_survives_the_pick():
    resolver = FakeResolver({"specs": {"colour|black": {
        "status": "exact", "specs": [{"attribute": "colour", "comparator": "eq", "value": "black"}]}}})
    after, _ = _picked(ask(act="new_topic", domain="inventory", specs=[spec(attribute="colour", value_key="black")]),
                       resolver=resolver)
    assert [(s.attribute, s.value) for s in after.held.specs] == [("colour", "black")]


def test_r2_6_the_pick_does_not_resolve_the_origins_phrase_again():
    resolver = _ambiguous_resolver()
    _picked(_origin_ask(), resolver=resolver)
    assert "product|mou" not in resolver.entity_lookups, "the picked entity replaces the ambiguous phrase"


def test_r2_6_a_pick_with_no_origin_and_no_deferred_runs_as_before():
    st = mk_state(mk_held("inventory", {"product": ["OTHER1"]}),
                  pending_=PendingQuestion(kind="clarify_entity", asked="Which?", asked_turn=3, options=[
                      Option(key="1", label="SRTWC286", payload={"key": "SRTWC286", "kind": "product", "label": "SRTWC286", "op": "set"})]))
    after, actions = apply_turn(st, _pick(), resolver=FakeResolver({}), now=NOW)
    assert isinstance(actions[0], Lookup) and after.held.domain == "inventory"
    assert [r.key for r in after.held.refs["product"]] == ["SRTWC286"]


# ------------------------------------------------------------------ R2-7 old state


def _old_state_raw() -> dict:
    rows = replay.load_turns(replay.FIXTURE_DIR / "livecheck_r7_r8.jsonl")
    raw = copy.deepcopy(next(r for r in rows if r["case"] == "r8-2-eta-mou-pick-1")["state_in"])
    raw["pending"]["deferred"] = {"domain": "incoming", "attachment_types": [], "quantity": None}
    assert "origin" not in raw["pending"] and "sid" not in raw["held"] and "next_sid" not in raw
    return raw


def test_r2_7_a_state_saved_before_r2_loads_with_empty_defaults():
    st = load_state(_old_state_raw(), NOW)
    assert st.held.domain == "inventory" and st.pending.deferred["domain"] == "incoming"
    assert st.pending.origin is None and st.held.sid is None
    assert isinstance(st.next_sid, int)


def test_r2_7_a_pick_on_an_old_state_with_deferred_answers_the_deferred_ask():
    rows = replay.load_turns(replay.FIXTURE_DIR / "livecheck_r7_r8.jsonl")
    parsed = ParserV2Output.model_validate(next(r for r in rows if r["case"] == "r8-2-eta-mou-pick-1")["parser_out"])
    after, actions = apply_turn(load_state(_old_state_raw(), NOW), parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(actions[0], Lookup)
    assert after.held.domain == "incoming"
    assert [r.key for r in after.held.refs["product"]] == ["SRTWC286"]


def test_r2_7_an_old_state_round_trips_through_dump_and_load():
    st = load_state(_old_state_raw(), NOW)
    assert load_state(dump_state(st), NOW) == st


def test_r2_7_sids_and_origin_round_trip_through_dump_and_load():
    st = _state_with(mk_held("inventory", {"product": ["A1"]}, sid="s2"),
                     topics=[mk_held("order", {"product": ["B1"]}, sid="s1")], next_sid=3,
                     pending_=_entity_pending(ask(act="unclear", domain="incoming")))
    back = load_state(dump_state(st), NOW)
    assert back == st
    assert back.held.sid == "s2" and back.topics[0].sid == "s1" and back.next_sid == 3
    assert Ask.model_validate(back.pending.origin).domain == "incoming"


# ------------------------------------------------------------------ R2-2 through the engine, R2-8 other answer paths

from tests.chatbot.pv2.test_core_engine_hook import FakeResult, fake_turn  # noqa: E402,F401 - fixture
from tests.chatbot.test_customer_scope_lane import _link_customers  # noqa: E402
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access, stub_parser  # noqa: E402,F401
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact  # noqa: E402
from sorento_crm_mcp.presenters import present_response  # noqa: E402


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


def test_r2_2_the_engine_writes_the_printing_subjects_sid_into_the_register(session_factory, monkeypatch, fake_turn):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, "ZZT OWN A")
    _go_v2(session_factory)
    verdict = _parser_output(asks=None, topic_reset=None, domain_hint="order", intent_hint="check_order",
                             order_status=None, entities=[], date_filter_start="2026-09-01",
                             date_filter_end="2026-09-30")
    st = _state_with(mk_held("order", {}, sid="s4"), next_sid=5)
    fake_turn(FakeResult(verdict=verdict, state=st))
    rendered = present_response(E.ORDERS_TOOL, json.dumps(E.ORDERS_RAW))
    _run_turn(session_factory, monkeypatch, qf=verdict, text_body="my orders",
              msg_id=f"ZZT-r2-{uuid.uuid4().hex[:8]}", attributes=E.ALL_REVEALS, matches={}, mcp_response=rendered)
    saved = _saved(session_factory)["pv2"]
    assert saved["register"], "the order list was printed"
    assert {e["subject"] for e in saved["register"]} == {"s4"}
    assert saved["held"]["sid"] == "s4"


# R2-8 path 1 (named): engine.py `_pv2_write_register` before the main `_run_answer` (engine.py:7363) -> R1 tests.
# R2-8 path 2 (named): the stock-ask render, `stock_availability` entries (engine.py:7204 `_stock_ask_answered_entries`).


def test_r2_8_the_stock_ask_render_records_the_products_it_printed():
    env = {"stock_availability": [
        {"product_code": "SRTWC286", "product_name": "SRTWC286", "requested_qty": 5, "branch": "in_stock"},
        {"product_code": "SRTWB7109", "product_name": "SRTWB7109", "requested_qty": 2, "branch": "incoming"},
    ]}
    entries = register.entries_from_envelopes([env], 3)
    assert [(e.kind, e.key, e.label) for e in entries] == [
        ("product", "SRTWC286", "SRTWC286"), ("product", "SRTWB7109", "SRTWB7109")]


def test_r2_8_a_stock_ask_entry_still_owing_its_quantity_is_not_recorded():
    env = {"stock_availability": [
        {"product_code": "SRTWC286", "product_name": "SRTWC286", "requested_qty": None, "branch": None}]}
    assert register.entries_from_envelopes([env], 3) == []


# R2-8 path 3 (named): the PV2 report direct reply, `pv2/answer.py:258` (`build_result` report branch, then
# `engine._pv2_direct_reply`): the ranked rows it prints are `st.last_rows`; they must be the register too.


def _report_lookup() -> Lookup:
    rs = ReportSpec(dataset="sales", measures=["amount"], group_by=["customer"], top_n_per_group=None, filters=[],
                    period=None, compare_to=None, compare_period=None, grain=None, threshold=None, sort="desc",
                    top_n=2, basis="delivered")
    return Lookup(ask_index=0, domain="sales", held=Held(domain="sales", report=rs), ask=ask(act="new_topic", domain="sales"))


def _report_runner(rows):
    def run(spec_, refs, today, row, incoming):
        return SimpleNamespace(text="Top 2 customers\n1. ABC SDN BHD\n2. XYZ", plan=SimpleNamespace(spec=spec_, status="run"),
                               rows=rows, pending=None)

    return run


def test_r2_8_a_report_direct_reply_writes_its_printed_rows_to_the_register():
    from app.services.chatbot.pv2 import reports

    rows = [option("ABC SDN BHD", "ABC SDN BHD", {reports.ROW_PAYLOAD: {"dimension": "customer", "keys": ["c1"], "label": "ABC SDN BHD"}}),
            option("XYZ", "XYZ", {reports.ROW_PAYLOAD: {"dimension": "customer", "keys": ["c2", "c3"], "label": "XYZ"}})]
    st = mk_state(Held(domain="sales"))
    result = answer.build_result(st, [_report_lookup()], out(ask(act="new_topic", domain="sales")), today=TODAY,
                                 report_answer=_report_runner(rows))
    assert result.direct_reply and result.route == "report"
    assert [(e.kind, e.label) for e in result.state.register] == [("customer", "ABC SDN BHD"), ("customer", "XYZ")]
    assert all(e.turn == result.state.turn_no for e in result.state.register)


def test_r2_8_a_report_that_printed_no_rows_keeps_the_previous_entries():
    prev = [RegisterEntry(id="R1", kind="product", key="A1", label="A1", parent=None, subject="s1", turn=2)]
    st = mk_state(Held(domain="sales"))
    st.register = list(prev)
    result = answer.build_result(st, [_report_lookup()], out(ask(act="new_topic", domain="sales")), today=TODAY,
                                 report_answer=_report_runner([]))
    assert result.state.register == prev


# R2-8 path 4 (named): the entities-only arm, `engine._run_entities_only_arm` (engine.py:6144), which answers the bare
# codes through `_run_answer` without the main path's `_pv2_write_register` call.


def test_r2_8_the_entities_only_arm_writes_the_codes_it_answered_to_the_register(session_factory, monkeypatch, fake_turn):
    from tests.chatbot.test_entities_only_arm import MATCHES, PRODUCT_A_CODE, PRODUCT_B_CODE

    _seed_contact(session_factory, variables={})
    _go_v2(session_factory)
    entities = [{"raw": c, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
                for c in (PRODUCT_A_CODE, PRODUCT_B_CODE)]
    qf = _parser_output(asks=None, topic_reset=None, domain_hint=None, intent_hint=None, entities=entities)
    fake_turn(FakeResult(verdict=qf, state=mk_state()))
    result, _ = _run_turn(session_factory, monkeypatch, qf=qf, text_body=f"{PRODUCT_A_CODE}, {PRODUCT_B_CODE}",
                          msg_id=f"ZZT-r2-{uuid.uuid4().hex[:8]}", matches=MATCHES)
    assert PRODUCT_A_CODE in ((result.reply or {}).get("text") or ""), result.reply
    register_saved = _saved(session_factory)["pv2"]["register"]
    assert [(e["kind"], e["label"]) for e in register_saved] == [("product", PRODUCT_A_CODE), ("product", PRODUCT_B_CODE)]


# ------------------------------------------------------------------ R2-9 the Products field hidden drops order lines


def test_r2_9_the_structurer_drops_order_lines_when_the_products_field_is_hidden():
    hidden = [a for a in E.ALL_REVEALS if a != "delivery_orders.products"]
    figures = E.structured(E.ORDERS_TOOL, E.ORDERS_RAW, access=hidden)["answers"]
    assert figures and all("lines" not in f for f in figures), [f.get("lines") for f in figures]
    assert not any(fld.get("key") == "products" for f in figures for fld in f["fields"])


def test_r2_9_the_structurer_keeps_order_lines_when_the_products_field_is_revealed():
    figures = E.structured(E.ORDERS_TOOL, E.ORDERS_RAW, access=E.ALL_REVEALS)["answers"]
    assert [[ln["product_code"] for ln in f["lines"]] for f in figures] == [["SRTWB1096", "SRTWT7203"], ["SRTW5000"]]


def test_r2_9_a_hidden_products_field_records_no_product_children_end_to_end():
    hidden = [a for a in E.ALL_REVEALS if a != "delivery_orders.products"]
    env = E.envelope(E.ORDERS_TOOL, E.ORDERS_RAW, domain="order", access=hidden)
    assert [e.kind for e in register.entries_from_envelopes([env], 3)] == ["delivery_order", "delivery_order"]


# ------------------------------------------------------------------ R2-10 printed_rows aligned with figures


def test_r2_10_printed_rows_stay_aligned_when_a_non_dict_answer_is_dropped():
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.turn.plan import FetchSpec

    fetched = copy.deepcopy(E.structured(E.ORDERS_TOOL, E.ORDERS_RAW, access=E.ALL_REVEALS))
    assert fetched["printed_rows"] == [0, 1]
    fetched["answers"] = ["not a card", *fetched["answers"]]
    fetched["printed_rows"] = [1, 2]  # the structurer indexes `answers`, junk row included
    env = turn_runtime.envelope_of({"fetch": fetched}, FetchSpec(domain="order", entities=[], filters={}, date_window=None), [])
    assert len(env["figures"]) == 2
    entries = register.entries_from_envelopes([env], 3)
    assert [e.label for e in entries if e.kind == "delivery_order"] == ["202609-2012", "202609-2013"]
    assert all(0 <= i < len(env["figures"]) for i in env["printed_rows"])


def test_r2_10_a_non_dict_answer_after_the_cards_changes_nothing():
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.turn.plan import FetchSpec

    fetched = copy.deepcopy(E.structured(E.ORDERS_TOOL, E.ORDERS_RAW, access=E.ALL_REVEALS))
    fetched["answers"] = [*fetched["answers"], None]
    env = turn_runtime.envelope_of({"fetch": fetched}, FetchSpec(domain="order", entities=[], filters={}, date_window=None), [])
    entries = register.entries_from_envelopes([env], 3)
    assert [e.label for e in entries if e.kind == "delivery_order"] == ["202609-2012", "202609-2013"]


# ------------------------------------------------------------------ R2-11 v1 turns unchanged


def test_r2_11_a_v1_mode_turn_saves_no_pv2_state_at_all(session_factory, seeded, stub_parser, stub_access):
    from app.services.chatbot import engine as engine_mod

    stub_parser()
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert result.status == "done"
    assert "pv2" not in _saved(session_factory)
