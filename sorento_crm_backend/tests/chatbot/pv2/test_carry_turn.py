"""run_turn_v2 / after_v1 with fake parse, resolver and exchange loader (no DB, no model)."""
from __future__ import annotations

from datetime import timedelta

from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import Answer
from app.services.chatbot.pv2.state import Lookup, ResolvedRef, dump_state, load_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held, mk_state, option, out, pending

SRT = {"entities": {"product|SRTWC286": {"status": "exact", "refs": [{"kind": "product", "key": "SRTWC286", "label": "SRTWC286"}]}}}


class Spy:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []
        self.since = []

    def parse(self, messages, user_message):
        self.calls.append((messages, user_message))
        return self.parsed

    def exchanges(self, since):
        self.since.append(since)
        return [("hi", "hello"), ("stock?", "which product?")]


def _ctx(spy, raw_state, text="stock of SRTWC286", resolver=None, **kw):
    return turn.TurnV2Context(text=text, now=NOW, raw_state=raw_state, profile=MemoryProfile(audience="staff"),
                              parse=spy.parse, resolver=resolver or FakeResolver(SRT),
                              load_exchanges=spy.exchanges, **kw)


def _lookup_out():
    return out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="SRTWC286")]))


def test_memory_given_to_the_parser_carries_the_held_subject_and_the_message():
    spy = Spy(_lookup_out())
    h = held("product_attachment", {"product": ["SRTWC6003"]})
    h.refs["product"][0] = ResolvedRef(kind="product", key="uid-1", label="SRTWC6003 Family")
    r = turn.run_turn_v2(_ctx(spy, dump_state(mk_state(h))))
    messages, user_message = spy.calls[0]
    assert "HELD: domain=product_attachment" in user_message and "SRTWC6003 Family" in user_message
    assert "uid-1" not in user_message
    assert user_message.endswith("<message>stock of SRTWC286</message>")
    assert [m["content"] for m in messages] == ["hi", "hello", "stock?", "which product?"]
    assert r.route == "lookup" and r.verdict["domain_hint"] == "inventory"
    assert r.trace["memory"] == user_message


def test_a_gap_over_four_hours_rolls_before_the_parser_sees_memory():
    spy = Spy(_lookup_out())
    old = mk_state(held("product_attachment", {"product": ["SRTWC6003"]}),
                   pending_=pending("clarify_entity", [option("a", "A")]),
                   last_rows=[option("1", "SRTWC6003")], last_turn_at=NOW - timedelta(hours=5))
    r = turn.run_turn_v2(_ctx(spy, dump_state(old)))
    user_message = spy.calls[0][1]
    assert "HELD: none" in user_message and "PENDING QUESTION: none" in user_message
    assert "LAST ANSWER ROWS: none" in user_message
    assert "  T1: product_attachment | product: SRTWC6003" in user_message
    assert spy.since == [NOW]  # history is this (new) session only
    assert r.state.session_started_at == NOW


def test_a_three_hour_gap_keeps_the_session():
    spy = Spy(_lookup_out())
    old = mk_state(held("product_attachment", {"product": ["SRTWC6003"]}), last_turn_at=NOW - timedelta(hours=3))
    turn.run_turn_v2(_ctx(spy, dump_state(old)))
    assert "HELD: domain=product_attachment" in spy.calls[0][1]
    assert spy.since == [old.session_started_at]


def test_result_state_is_stamped_and_advanced_and_round_trips():
    spy = Spy(_lookup_out())
    old = mk_state(turn_no=4, last_turn_at=NOW - timedelta(minutes=10))
    r = turn.run_turn_v2(_ctx(spy, dump_state(old)))
    assert r.state.last_turn_at == NOW and r.state.turn_no == 5
    assert load_state(turn.wire(r), NOW) == r.state
    assert r.state.held.refs["product"][0].key == "SRTWC286"


def test_missing_or_garbage_state_starts_fresh():
    for raw in (None, "junk", {"v": 9}):
        spy = Spy(_lookup_out())
        r = turn.run_turn_v2(_ctx(spy, raw))
        assert r.state.turn_no == 1 and "HELD: none" in spy.calls[0][1]


def test_trace_records_history_count_and_route():
    spy = Spy(_lookup_out())
    r = turn.run_turn_v2(_ctx(spy, None))
    t = r.trace["pv2_turn"]
    assert t["history_exchanges"] == 2 and t["route"] == "lookup" and t["held_domain"] == "inventory"
    assert r.trace["parser_out"]["language"] == "en"


def test_a_clarify_turn_returns_direct_reply_and_records_pending():
    spy = Spy(out(ask(act="unclear")))
    r = turn.run_turn_v2(_ctx(spy, None))
    assert r.route == "clarify" and r.verdict is None and r.direct_reply
    assert r.state.pending is not None


def test_staff_second_miss_reasks_instead_of_offering_a_handover():
    p = pending("clarify_spec_attribute", [option("dim_length", "Length")], misses=1)
    spy = Spy(out(ask(act="answers_bot_question", answer=Answer(option_key="nope", free_value=None))))
    r = turn.run_turn_v2(_ctx(spy, dump_state(mk_state(held("product_attachment", {"product": ["A"]}), pending_=p)),
                              can_offer_handover=False))
    assert r.actions[0].type == "clarify"
    spy2 = Spy(out(ask(act="answers_bot_question", answer=Answer(option_key="nope", free_value=None))))
    r2 = turn.run_turn_v2(_ctx(spy2, dump_state(mk_state(held("product_attachment", {"product": ["A"]}), pending_=p))))
    assert r2.actions[0].type == "offer_handover"


# -- after_v1 ------------------------------------------------------------------------------ #


def test_after_v1_records_the_v1_question_with_positional_keys():
    st = mk_state(held("inventory", {"product": ["A"]}), turn_no=6)
    new = turn.after_v1(st, question_kind="pick_row", option_labels=["Alpha", "Beta"], asked="Which one?")
    q = new.pending
    assert q.kind == "v1_open_question" and q.v1_kind == "pick_row" and q.asked == "Which one?"
    assert [(o.key, o.label) for o in q.options] == [("1", "Alpha"), ("2", "Beta")]
    assert q.asked_turn == 6 and q.misses == 0
    assert st.pending is None  # input untouched


def test_after_v1_offer_adds_yes_no():
    new = turn.after_v1(mk_state(), question_kind="offer", option_labels=["A"], offer=True, language="zh")
    assert [o.key for o in new.pending.options] == ["1", "yes", "no"]
    assert [o.label for o in new.pending.options][1:] == ["是", "否"]


def test_after_v1_clears_only_a_v1_question_when_nothing_was_asked():
    v1 = mk_state(pending_=pending("v1_open_question", [option("1", "A")]))
    assert turn.after_v1(v1, question_kind=None, option_labels=[]).pending is None
    other = mk_state(pending_=pending("clarify_entity", [option("a", "A")]))
    assert turn.after_v1(other, question_kind=None, option_labels=[]).pending is not None


def test_after_v1_rows_are_capped_at_twenty_and_none_keeps_them():
    st = mk_state(last_rows=[option("1", "keep")])
    assert [o.label for o in turn.after_v1(st, question_kind=None, option_labels=[]).last_rows] == ["keep"]
    rows = [option(str(i), f"R{i}") for i in range(1, 26)]
    assert len(turn.after_v1(st, question_kind=None, option_labels=[], rows=rows).last_rows) == 20


def test_a_v1_question_is_answered_by_option_key_and_passes_through_to_the_bridge():
    base = mk_state(held("inventory", {"product": ["SRTWC286"]}))
    st = turn.after_v1(base, question_kind="pick_row", option_labels=["Alpha", "Beta"], asked="Which one?")
    o = out(ask(act="answers_bot_question", answer=Answer(option_key="2", free_value=None)))
    new, actions = apply_turn(st, o, resolver=FakeResolver())
    assert isinstance(actions[0], Lookup) and new.pending is None
    assert actions[0].pending_kind == "v1_open_question" and actions[0].picked.key == "2"
    v = bridge.to_v1_verdict(new, actions[0], today=NOW.date())
    assert v["open_question_answer"]["mode"] == "pick" and v["open_question_answer"]["picked"] == [2]
