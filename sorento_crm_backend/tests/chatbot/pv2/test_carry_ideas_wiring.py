"""CARRY -> IDEAS wiring (#1641): `run_idea_turn(db, *, ask, language, respond_io_id,
message_text, session_vars, pending, turn_no, is_test=False, submitter_name=None)`, its
`pending` landing on state.pending and its `idea_draft_id` on Held.idea_draft_id, and `None`
(not an idea turn) falling through to the ordinary answer."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.chatbot.pv2 import answer, ideas, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import Answer
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, mk_state, option, out, pending

TODAY = NOW.date()
SIMILAR = pending(
    "idea_similar",
    [
        option("1", "Dark mode for the dealer portal", {"choice": 1}),
        option("2", "Night theme for the app", {"choice": 2}),
        option("new", "New idea", {"choice": "new"}),
    ],
)


def _handler(calls):
    return answer.default_idea_answer(
        "DB", respond_io_id="r-9", message_text="yang kedua", session_vars={"ideation": {"status": "similar_offered"}},
        is_test=True, submitter_name="Ahmad",
    )


def test_a_similar_pick_by_words_reaches_run_idea_turn_with_the_contract_kwargs(monkeypatch):
    calls = []

    def fake_run(db, **kw):
        calls.append((db, kw))
        return ideas.IdeaTurnResult(
            status="similar_picked", reply_text="Linked to IDEA-12.", language="ms",
            session_vars={"ideation": {"status": "captured", "idea_id": "i-12"}}, pending=None, idea_draft_id="i-12",
        )

    monkeypatch.setattr(ideas, "run_idea_turn", fake_run)
    o = out(ask(act="answers_bot_question", answer=Answer(option_key="2", free_value=None)), language="ms")
    state, actions = apply_turn(mk_state(pending_=SIMILAR), o, resolver=FakeResolver(), now=NOW)
    r = answer.build_result(state, actions, o, today=TODAY, pending_in=SIMILAR, idea_answer=_handler(calls))

    (db, kw), = calls
    assert db == "DB"
    assert set(kw) == {"ask", "language", "respond_io_id", "message_text", "session_vars", "pending",
                       "turn_no", "is_test", "submitter_name"}
    assert kw["ask"].answer.option_key == "2" and kw["pending"] is SIMILAR
    assert kw["language"] == "ms" and kw["respond_io_id"] == "r-9" and kw["submitter_name"] == "Ahmad"
    assert kw["turn_no"] == state.turn_no and kw["is_test"] is True
    assert r.route == "idea" and r.direct_reply == "Linked to IDEA-12."
    assert r.state.held.idea_draft_id == "i-12" and r.state.pending is None
    assert r.trace["ideation_session_vars"] == {"ideation": {"status": "captured", "idea_id": "i-12"}}


def test_an_idea_pending_from_ideas_lands_on_state(monkeypatch):
    asked = {"kind": "idea_multi", "asked": "I read 2 ideas in this message.",
             "options": [{"key": "1", "label": "Save as one idea", "payload": {"choice": "one"}},
                         {"key": "2", "label": "Send them one at a time", "payload": {"choice": "separate"}}]}
    monkeypatch.setattr(ideas, "run_idea_turn", lambda db, **kw: ideas.IdeaTurnResult(
        status="idea_multi_offered", reply_text="I read 2 ideas in this message.\n1. Save as one idea",
        language="en", session_vars={}, pending=asked, idea_draft_id=None))
    o = out(ask(act="new_topic", domain="ideate"))
    state, actions = apply_turn(mk_state(), o, resolver=FakeResolver(), now=NOW)
    r = answer.build_result(state, actions, o, today=TODAY, idea_answer=_handler([]))
    assert r.route == "idea" and r.state.pending.kind == "idea_multi"
    assert [x.key for x in r.state.pending.options] == ["1", "2"]


def test_not_an_idea_turn_falls_through_to_the_ordinary_answer(monkeypatch):
    monkeypatch.setattr(ideas, "run_idea_turn", lambda db, **kw: None)
    o = out(ask(act="new_topic", domain="ideate"))
    state, actions = apply_turn(mk_state(), o, resolver=FakeResolver(), now=NOW)
    r = answer.build_result(state, actions, o, today=TODAY, idea_answer=_handler([]))
    assert r.route == "lookup" and r.verdict["domain_hint"] == "ideate"


@pytest.mark.parametrize("contact, expected", [({"firstName": "Ahmad", "id": 1}, "Ahmad"), ({"id": 1}, None)])
def test_the_submitter_is_the_contact_first_name_as_v1_sends_it(contact, expected):
    assert turn._first_name(SimpleNamespace(contact=contact)) == expected
