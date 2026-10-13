"""Unit tests of the implemented state.py helpers (contract section 3)."""
from __future__ import annotations

import json
from datetime import timedelta

from app.services.chatbot.pv2.schema import Period
from app.services.chatbot.pv2.state import (
    SESSION_GAP,
    TOPICS_CAP,
    ConversationState,
    Held,
    ResolvedRef,
    apply_payload,
    dump_state,
    load_state,
    new_state,
    push_topic,
    roll_session,
    set_refs,
    topic_index,
    topic_key,
)
from tests.chatbot.pv2.carry_fakes import NOW, held, mk_state, option, pending


def R(kind, key):
    return ResolvedRef(kind=kind, key=key, label=key)


# -- roll_session ------------------------------------------------------------ #


def test_roll_keeps_session_at_exactly_four_hours():
    s = mk_state(held("inventory", {"product": ["A"]}), last_turn_at=NOW - SESSION_GAP)
    assert roll_session(s, NOW) is s


def test_roll_after_gap_moves_held_to_topics_and_clears():
    s = mk_state(
        held("inventory", {"product": ["A"]}),
        pending_=pending("clarify_entity", [option("a", "A")]),
        topics=[held("sales", {"brand": ["B"]})],
        last_rows=[option("1", "A")],
        last_turn_at=NOW - SESSION_GAP - timedelta(seconds=1),
    )
    r = roll_session(s, NOW)
    assert r.held.is_empty() and r.pending is None and r.last_rows == []
    assert [t.domain for t in r.topics] == ["inventory", "sales"]
    assert r.session_started_at == NOW
    assert s.pending is not None and s.held.domain == "inventory"  # input untouched


def test_roll_with_empty_held_adds_no_topic():
    s = mk_state(last_turn_at=NOW - timedelta(hours=9))
    assert roll_session(s, NOW).topics == []


# -- push_topic / topic_key / topic_index ------------------------------------ #


def test_push_topic_ignores_empty_and_copies():
    t = [held("sales")]
    r = push_topic(t, Held())
    assert r == t and r is not t


def test_push_topic_dedupes_to_front():
    a, b = held("sales", {"brand": ["A"]}), held("inventory", {"product": ["P"]})
    r = push_topic([a, b], b)
    assert r == [b, a]


def test_push_topic_cap_newest_first():
    topics: list[Held] = []
    for i in range(7):
        topics = push_topic(topics, held("d", {"product": [f"P{i}"]}))
    assert len(topics) == TOPICS_CAP == 5
    assert [t.refs["product"][0].key for t in topics] == ["P6", "P5", "P4", "P3", "P2"]


def test_topic_key_and_index_exact_only():
    topics = [held("a"), held("b")]
    assert topic_key(0) == "T1" and topic_key(1) == "T2"
    assert topic_index("T1", topics) == 0
    assert topic_index("T2", topics) == 1
    for bad in ("t1", "T 1", "T01", " T1", "T3", "T0", "", None, "1"):
        assert topic_index(bad, topics) is None
    assert topic_index("T1", []) is None


# -- apply_payload ------------------------------------------------------------ #


def test_payload_attachment_type_and_domain():
    h = held("a")
    assert apply_payload(h, {"kind": "attachment_type", "key": "certification"}).attachment_type == "certification"
    assert apply_payload(h, {"kind": "domain", "key": "sales"}).domain == "sales"
    assert h.domain == "a" and h.attachment_type is None


def test_payload_spec_replaces_same_attribute_keeps_others():
    h = held("a", specs=[("trap_type", "p_trap"), ("dim_length", 500)])
    r = apply_payload(h, {"kind": "spec", "attribute": "dim_length", "value": 600, "unit": "mm"})
    assert [(s.attribute, s.value) for s in r.specs] == [("trap_type", "p_trap"), ("dim_length", 600)]
    assert r.specs[-1].comparator == "eq" and r.specs[-1].unit == "mm"


def test_payload_ref_set_default_and_add():
    h = held("a", {"product": ["A"]})
    s = apply_payload(h, {"kind": "product", "key": "B", "label": "Bee"})
    assert [r.key for r in s.refs["product"]] == ["B"] and s.refs["product"][0].label == "Bee"
    a = apply_payload(h, {"kind": "product", "key": "B", "op": "add"})
    assert [r.key for r in a.refs["product"]] == ["A", "B"] and a.refs["product"][1].label == "B"
    assert [r.key for r in h.refs["product"]] == ["A"]


def test_payload_scalar_kinds_and_missing_kind_change_nothing():
    h = held("a", {"product": ["A"]})
    for p in ({}, {"kind": "handover", "value": True}, {"kind": "choice", "key": "x"}, {"kind": "row", "key": "1"}):
        r = apply_payload(h, p)
        assert r == h and r is not h


# -- set_refs ------------------------------------------------------------------ #


def test_set_refs_ops():
    base = {"product": [R("product", "A"), R("product", "B")], "brand": [R("brand", "X")]}
    assert [r.key for r in set_refs(base, "product", [R("product", "C")], "set")["product"]] == ["C"]
    assert [r.key for r in set_refs(base, "product", [R("product", "B"), R("product", "C")], "add")["product"]] == ["A", "B", "C"]
    assert [r.key for r in set_refs(base, "product", [R("product", "A")], "remove")["product"]] == ["B"]
    cleared = set_refs(base, "product", [], "clear")
    assert "product" not in cleared and "brand" in cleared
    assert [r.key for r in base["product"]] == ["A", "B"]  # input untouched


def test_set_refs_empty_result_drops_kind_and_dedupes():
    base = {"product": [R("product", "A")]}
    assert set_refs(base, "product", [R("product", "A")], "remove") == {}
    assert set_refs({}, "product", [R("product", "A"), R("product", "A")], "set")["product"] == [R("product", "A")]
    assert set_refs({}, "product", [R("product", "A")], "remove") == {}


def test_held_is_empty():
    assert Held().is_empty()
    assert not Held(period=Period(preset="today", n=None, date_from=None, date_to=None)).is_empty()
    assert not Held(idea_draft_id="x").is_empty()
    assert Held(refs={"product": []}).is_empty()


# -- load / dump ---------------------------------------------------------------- #


def test_load_state_garbage_is_fresh():
    for raw in (None, "x", 5, [], {}, {"v": 2}, {"v": 1, "turn_no": "abc"}, {"v": 1, "session_started_at": "nope"}):
        s = load_state(raw, NOW)
        assert s.turn_no == 0 and s.held.is_empty() and s.pending is None
        assert s.session_started_at == NOW and s.last_turn_at == NOW


def test_dump_load_round_trip():
    s = mk_state(
        held("product_attachment", {"product": ["A"]}, specs=[("dim_length", 600)], attachment_type="certification",
             period=Period(preset="last_month", n=None, date_from=None, date_to=None)),
        pending_=pending("clarify_entity", [option("a", "A", {"kind": "product", "key": "A"})], misses=1),
        topics=[held("sales", {"brand": ["B"]})],
        last_rows=[option("1", "A")],
    )
    raw = dump_state(s)
    json.dumps(raw)
    assert load_state(raw, NOW) == s
    assert isinstance(load_state(raw, NOW), ConversationState)
    assert new_state(NOW).turn_no == 0
