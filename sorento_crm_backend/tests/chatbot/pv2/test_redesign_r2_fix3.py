"""PV2 redesign R2, fix round 3: a re-started subject takes back its topic's sid; a register entry with no subject
carries no label. Red tests on the real `apply_turn` / register path, no model call."""
from __future__ import annotations

from tests.chatbot.pv2 import _register_envelopes as E  # loads the worktree MCP first

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.pv2 import register
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import RegisterEntry, dump_state, load_state, new_state, subject_label
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, out

R = FakeResolver({"entities": {f"product|{k}": {"status": "exact", "refs": [{"kind": "product", "key": k, "label": k}]}
                               for k in ("A1", "B1", "C1")}})


def _stock(k):
    return out(ask(act="new_topic", domain="inventory", entities=[entity("product", key=k)]))


def _turns(*keys):
    st = new_state(NOW)
    seen = []
    for k in keys:
        st, _ = apply_turn(st, _stock(k), resolver=R, now=NOW)
        seen.append(st)
    return seen


def test_p1_a_restarted_subject_takes_back_its_topics_sid_at_start():
    s1, s2, s3 = _turns("A1", "B1", "A1")
    first_sid = s1.held.sid
    assert s3.held.sid == first_sid, "the subject that came back is the same subject"
    assert [t.sid for t in s3.topics] == [s2.held.sid], "it left topics when it became held again"


def test_p1_after_the_next_push_the_restarted_subject_is_s2_and_topics_have_no_duplicate():
    s1, _, _, s4 = _turns("A1", "B1", "A1", "C1")
    assert subject_label(s4, s1.held.sid) == "S2"
    keys = [[r.key for r in t.refs["product"]] for t in s4.topics]
    assert keys == [["A1"], ["B1"]], keys
    assert len({t.sid for t in s4.topics}) == 2


def test_p2_register_entries_printed_under_the_restarted_subject_keep_a_label_after_the_next_push():
    _, _, s3 = _turns("A1", "B1", "A1")
    engine_mod._pv2_write_register({"pv2_state": s3}, [E.envelope(E.STOCK_TOOL, E.STOCK_RAW, domain="inventory")])
    assert s3.register, "the stock reply was printed"
    s4, _ = apply_turn(s3, _stock("C1"), resolver=R, now=NOW)
    for e in s3.register:
        assert subject_label(s4, e.subject) == "S2", (e.subject, s4.held.sid, [t.sid for t in s4.topics])


def test_p3_an_entry_written_with_no_held_subject_has_no_subject_not_the_string_s1():
    env = E.envelope(E.STOCK_TOOL, E.STOCK_RAW, domain="inventory")
    entries = register.entries_from_envelopes([env], 3, None)
    assert entries and all(e.subject is None for e in entries), [e.subject for e in entries]
    state = new_state(NOW)  # a fresh state: held is empty, no sid
    engine_mod._pv2_write_register({"pv2_state": state}, [env])
    assert state.register and all(e.subject is None for e in state.register)
    assert all(subject_label(state, e.subject) is None for e in state.register)


def test_p3_a_stored_entry_with_the_r1_era_subject_s1_still_loads():
    st = new_state(NOW)
    st.register = [RegisterEntry(id="R1", kind="product", key="A1", label="A1", parent=None, subject="S1", turn=2)]
    back = load_state(dump_state(st), NOW)
    assert back.register[0].subject == "S1"
