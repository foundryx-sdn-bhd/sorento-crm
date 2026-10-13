"""PV2-IDEAS red tests, spec section A: the two additive kwargs of `handle_capture_turn`.

`choice_in` (a parser-resolved pick that skips the digit/NEW text check) and `update_held`
(the v2 draft mechanics: draft_held / captured pointers). Both default off, and then every path
is byte-identical to v1 (the existing suite `tests/test_ideation_capture_turn.py` stays green).

The harness (Env + env fixture, real Postgres on a blank schema, the four outside seams
patched on the service module) is imported from that suite.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

# MUST be the first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402, F401

from tests.test_ideation_capture_turn import (  # noqa: E402, F401
    MSG,
    PROBLEM,
    TITLE,
    Env,
    _now_iso,
    _uid,
    env,
)

REPLY = "the problem is customers keep asking why the price differs from the sticker"


def _similar(n: int) -> list[dict]:
    return [
        {"idea_id": _uid(), "idea_number": f"IDEA-0{i}00", "title": f"Held similar {i}"}
        for i in range(1, n + 1)
    ]


def _go(env: Env, message: str, sv: dict | None = None, *, is_test: bool = False, **extra) -> dict:
    """One turn with the pointer landed where the mode reads it (live: the row; test: the caller)."""
    if sv is not None and not is_test:
        env.contact.session_vars = {**(env.contact.session_vars or {}), **sv}
        env.db.flush()
        sv = None
    return env.raw_turn(message, session_vars_in=sv, is_test=is_test, **extra)


def _draft(*, message: str = "i have an idea about prices", fields: dict | None = None,
           title: str = "Held title", age: timedelta = timedelta(0), is_test: bool = False) -> dict:
    return {"ideation": {
        "status": "draft_held",
        "message_text": message,
        "fields": fields if fields is not None else {"department": "Sales"},
        "title": title,
        "updated_at": _now_iso(age),
        "is_test": is_test,
    }}


def _intent_only(env: Env, text: str = "i have an idea about prices", *, language: str | None = None,
                 title: str = "Held title", extra: dict | None = None) -> str:
    """An ask_idea message: has_idea False, a problem the extractor wrote anyway, one extra field."""
    env.extraction(text, fields={"problem": "ignored", **(extra or {"department": "Sales"})},
                   title=title, language=language)
    env.extractions[text].has_idea = False
    return text


# --------------------------------------------------------------------------- #
# choice_in                                                                   #
# --------------------------------------------------------------------------- #
def test_choice_in_2_picks_second_without_text_check_or_extractor(env):
    env.ready()
    similar = _similar(3)
    sv = env.pointer(similar)
    out = _go(env, "yang kedua", sv, choice_in=2)
    assert out["status"] == "similar_picked"
    assert f"https://crm.example/ideas/{similar[1]['idea_id']}" in out["reply_text"]
    assert f"/ideas/{similar[0]['idea_id']}" not in out["reply_text"]
    assert f"/ideas/{similar[2]['idea_id']}" not in out["reply_text"]
    assert env.extractor_calls == []
    assert env.similar_calls == [] and env.create_calls == []


def test_choice_in_new_creates_from_held_message(env):
    env.ready()
    held = "chatbot should remember what the dealer asked before"
    ref = _uid()
    sv = env.pointer(_similar(3), message=held, intake_ref=ref)
    idea_id = env.created()
    out = _go(env, "yang baru", sv, choice_in="new")
    assert out["status"] == "complete"
    assert env.extractor_calls == []
    assert env.similar_calls == []
    assert len(env.create_calls) == 1
    p = env.create_calls[0]
    assert p["raw_transcript"] == held
    assert p["intake_ref"] == ref
    assert p["problem"] == PROBLEM
    assert f"/ideas/{idea_id}" in out["reply_text"]


@pytest.mark.parametrize("bad", [9, 0, 4, "second", "NEW"])
def test_choice_in_out_of_range_or_wrong_type_is_a_fresh_message(env, bad):
    env.ready()
    sv = env.pointer(_similar(3))
    env.extraction("yang kedua", fields={})
    out = _go(env, "yang kedua", sv, choice_in=bad)
    assert env.extractor_calls == ["yang kedua"]
    assert out["status"] == "ask_idea"
    assert env.create_calls == []


@pytest.mark.parametrize("choice", [2, "new"])
def test_choice_in_without_a_held_pointer_is_a_fresh_message(env, choice):
    env.ready()
    env.extraction("yang kedua", fields={})
    out = _go(env, "yang kedua", None, choice_in=choice)
    assert env.extractor_calls == ["yang kedua"]
    assert out["status"] == "ask_idea"
    assert env.create_calls == []


def test_without_choice_in_a_phrase_with_a_held_pointer_is_a_fresh_message(env):
    env.ready()
    env.extraction("yang kedua", fields={})
    out = _go(env, "yang kedua", env.pointer(_similar(3)))
    assert env.extractor_calls == ["yang kedua"]
    assert out["status"] == "ask_idea"


def test_choice_in_none_keeps_the_text_check(env):
    env.ready()
    out = _go(env, "2", env.pointer(_similar(3)), choice_in=None)
    assert out["status"] == "similar_picked"
    assert env.extractor_calls == []


def test_choice_in_pick_uses_held_language(env):
    env.ready()
    _go(env, "yang kedua", env.pointer(_similar(3), language="ms"), choice_in=1)
    assert env.languages == ["ms"]


def test_choice_in_does_not_bypass_the_access_gate(env):
    env.seed_workspace()
    env.seed_contact()
    out = _go(env, "yang kedua", env.pointer(_similar(3)), choice_in=2)
    assert out["status"] == "no_access"
    assert env.create_calls == []


# --------------------------------------------------------------------------- #
# update_held: ask_idea persists a draft_held pointer                         #
# --------------------------------------------------------------------------- #
def test_update_held_ask_idea_persists_draft_held_pointer(env):
    env.ready()
    text = _intent_only(env)
    out = env.raw_turn(text, update_held=True)
    assert out["status"] == "ask_idea"
    assert env.similar_calls == [] and env.create_calls == []
    ptr = env.persisted()["ideation"]
    assert ptr["status"] == "draft_held"
    assert ptr["message_text"] == text
    assert ptr["fields"] == {"department": "Sales"}
    assert "problem" not in ptr["fields"]
    assert ptr["title"] == "Held title"
    assert ptr["is_test"] is False
    assert ptr["updated_at"]
    assert "language" not in ptr
    assert out["session_vars"]["ideation"] == ptr


def test_update_held_ask_idea_pointer_carries_language(env):
    env.ready()
    text = _intent_only(env, language="ms")
    env.raw_turn(text, update_held=True)
    assert env.persisted()["ideation"]["language"] == "ms"


def test_update_held_ask_idea_with_no_fields_at_all_still_holds(env):
    env.ready()
    env.extraction("want to submit idea", fields={})
    out = env.raw_turn("want to submit idea", update_held=True)
    assert out["status"] == "ask_idea"
    ptr = env.persisted()["ideation"]
    assert ptr["status"] == "draft_held"
    assert ptr["message_text"] == "want to submit idea"
    assert ptr["fields"] == {}


def test_without_update_held_ask_idea_persists_no_pointer(env):
    env.ready()
    text = _intent_only(env)
    out = env.raw_turn(text)
    assert out["status"] == "ask_idea"
    assert "ideation" not in env.persisted()
    assert "ideation" not in out["session_vars"]


def test_update_held_false_explicit_is_v1(env):
    env.ready()
    text = _intent_only(env)
    out = env.raw_turn(text, update_held=False)
    assert "ideation" not in env.persisted()
    assert "ideation" not in out["session_vars"]


# --------------------------------------------------------------------------- #
# update_held + ask_reply: merge into the held draft                          #
# --------------------------------------------------------------------------- #
def test_update_held_ask_reply_merges_into_the_held_draft(env):
    env.ready()
    held_msg = "i have an idea about prices"
    merged = held_msg + "\n" + REPLY
    for key in (REPLY, merged):  # tolerate either extractor input; the spec pins only the payloads
        env.extraction(key, fields={"problem": PROBLEM}, title="")
    env.created()
    out = _go(env, REPLY, _draft(message=held_msg), ask_reply=True, update_held=True)

    assert out["status"] == "complete"
    assert len(env.create_calls) == 1
    p = env.create_calls[0]
    assert p["problem"] == PROBLEM
    assert p["department"] == "Sales"          # the held field survives
    assert p["title"] == "Held title"          # no new title: the held one
    assert p["raw_transcript"] == merged
    assert len(env.similar_calls) == 1
    assert env.similar_calls[0]["text"] == merged


def test_update_held_ask_reply_new_title_and_field_win(env):
    env.ready()
    held_msg = "i have an idea about prices"
    for key in (REPLY, held_msg + "\n" + REPLY):
        env.extraction(key, fields={"problem": PROBLEM, "department": "Finance"}, title="Fresh title")
    env.created()
    _go(env, REPLY, _draft(message=held_msg), ask_reply=True, update_held=True)
    p = env.create_calls[0]
    assert p["department"] == "Finance"
    assert p["title"] == "Fresh title"


def test_update_held_ask_reply_similar_found_holds_a_single_list(env):
    env.ready()
    held_msg = "i have an idea about prices"
    for key in (REPLY, held_msg + "\n" + REPLY):
        env.extraction(key, fields={"problem": PROBLEM}, title="")
    env.sim(2)
    out = _go(env, REPLY, _draft(message=held_msg), ask_reply=True, update_held=True)
    assert out["status"] == "similar_offered"
    assert env.create_calls == []
    held = env.persisted()["ideation"]
    assert held["status"] == "similar_offered"
    assert held["message_text"] == held_msg + "\n" + REPLY
    assert held["fields"]["department"] == "Sales"


def test_update_held_ask_reply_still_no_idea_gives_up_and_drops_pointer(env):
    env.ready()
    held_msg = "i have an idea about prices"
    for key in ("hmm not sure", held_msg + "\n" + "hmm not sure"):
        env.extraction(key, fields={})
    out = _go(env, "hmm not sure", _draft(message=held_msg, fields={}), ask_reply=True, update_held=True)
    assert out["status"] == "ask_idea_gave_up"
    assert "ideation" not in out["session_vars"]
    assert "ideation" not in env.persisted()
    assert env.similar_calls == [] and env.create_calls == []


def test_update_held_ask_reply_required_check_runs_on_merged_fields(env):
    """The held draft already carries a problem; a reply that adds only a department completes."""
    env.ready()
    held_msg = "price tag idea"
    reply = "it is for the sales team"
    for key in (reply, held_msg + "\n" + reply):
        env.extraction(key, fields={"department": "Sales"})
    env.created()
    out = _go(env, reply, _draft(message=held_msg, fields={"problem": PROBLEM}), ask_reply=True,
              update_held=True)
    assert out["status"] == "complete"
    assert env.create_calls[0]["problem"] == PROBLEM
    assert env.create_calls[0]["department"] == "Sales"


@pytest.mark.parametrize("held_kwargs", [
    {"age": timedelta(hours=25)},
    {"is_test": True},
])
def test_stale_or_other_mode_draft_is_ignored_reply_is_extracted_alone(env, held_kwargs):
    env.ready()
    env.extraction(REPLY, fields={"problem": PROBLEM}, title=TITLE)
    env.created()
    out = _go(env, REPLY, _draft(**held_kwargs), ask_reply=True, update_held=True)
    assert out["status"] == "complete"
    assert env.extractor_calls == [REPLY]
    p = env.create_calls[0]
    assert p["raw_transcript"] == REPLY
    assert "department" not in p
    assert p["title"] == TITLE


def test_draft_held_pointer_is_ignored_without_update_held(env):
    env.ready()
    env.extraction(REPLY, fields={"problem": PROBLEM}, title=TITLE)
    env.created()
    _go(env, REPLY, _draft(), ask_reply=True)
    assert env.create_calls[0]["raw_transcript"] == REPLY
    assert "department" not in env.create_calls[0]


# --------------------------------------------------------------------------- #
# update_held: captured pointers                                              #
# --------------------------------------------------------------------------- #
def test_update_held_complete_persists_captured_pointer(env):
    env.ready()
    env.idea_message()
    idea_id = env.created()
    out = env.raw_turn(MSG, update_held=True)
    assert out["status"] == "complete"
    ptr = env.persisted()["ideation"]
    assert ptr["status"] == "captured"
    assert ptr["idea_id"] == idea_id
    assert ptr["idea_number"] == "IDEA-0184"
    assert ptr["title"] == TITLE
    assert ptr["is_test"] is False
    assert ptr["updated_at"]
    assert set(ptr) == {"status", "idea_id", "idea_number", "title", "updated_at", "is_test"}
    assert out["session_vars"]["ideation"] == ptr


def test_without_update_held_complete_persists_no_pointer(env):
    env.ready()
    env.idea_message()
    env.created()
    out = env.raw_turn(MSG)
    assert out["status"] == "complete"
    assert "ideation" not in env.persisted()
    assert "ideation" not in out["session_vars"]


def test_update_held_new_from_held_list_persists_captured_pointer(env):
    env.ready()
    held = env.idea_message("chatbot should remember what the dealer asked before")
    sv = env.pointer(_similar(2), message=held)
    idea_id = env.created()
    out = _go(env, "new", sv, update_held=True)
    assert out["status"] == "complete"
    ptr = env.persisted()["ideation"]
    assert ptr["status"] == "captured" and ptr["idea_id"] == idea_id


def test_update_held_similar_picked_persists_captured_pointer_for_the_picked_idea(env):
    env.ready()
    similar = _similar(3)
    out = _go(env, "yang kedua", env.pointer(similar), choice_in=2, update_held=True)
    assert out["status"] == "similar_picked"
    ptr = env.persisted()["ideation"]
    assert ptr["status"] == "captured"
    assert ptr["idea_id"] == similar[1]["idea_id"]
    assert ptr["idea_number"] == similar[1]["idea_number"]
    assert ptr["title"] == similar[1]["title"]
    assert ptr["is_test"] is False
    assert ptr["updated_at"]


def test_without_update_held_similar_picked_drops_the_pointer(env):
    env.ready()
    out = _go(env, "yang kedua", env.pointer(_similar(3)), choice_in=2)
    assert out["status"] == "similar_picked"
    assert "ideation" not in env.persisted()
    assert "ideation" not in out["session_vars"]


def test_update_held_similar_offered_still_persists_the_list_pointer(env):
    env.ready()
    env.idea_message()
    env.sim(2)
    out = env.raw_turn(MSG, update_held=True)
    assert out["status"] == "similar_offered"
    assert env.persisted()["ideation"]["status"] == "similar_offered"


def test_update_held_keeps_other_session_vars_keys(env):
    env.ready(session_vars={"focus": {"x": 1}})
    env.idea_message()
    env.created()
    env.raw_turn(MSG, update_held=True)
    persisted = env.persisted()
    assert persisted["focus"] == {"x": 1}
    assert persisted["ideation"]["status"] == "captured"


# --------------------------------------------------------------------------- #
# update_held on a test turn                                                  #
# --------------------------------------------------------------------------- #
def test_update_held_test_turn_ask_idea_returns_pointer_but_persists_nothing(env):
    env.ready()
    text = _intent_only(env)
    out = env.raw_turn(text, is_test=True, update_held=True)
    assert out["status"] == "ask_idea"
    ptr = out["session_vars"]["ideation"]
    assert ptr["status"] == "draft_held"
    assert ptr["is_test"] is True
    assert ptr["message_text"] == text
    assert "ideation" not in env.persisted()


def test_update_held_test_turn_complete_returns_captured_pointer_but_persists_nothing(env):
    env.ready()
    env.idea_message()
    idea_id = env.created()
    out = env.raw_turn(MSG, is_test=True, update_held=True)
    assert out["status"] == "complete"
    ptr = out["session_vars"]["ideation"]
    assert ptr["status"] == "captured"
    assert ptr["idea_id"] == idea_id
    assert ptr["is_test"] is True
    assert "ideation" not in env.persisted()


def test_update_held_test_turn_ask_reply_reads_the_callers_pointer(env):
    env.ready()
    held_msg = "i have an idea about prices"
    for key in (REPLY, held_msg + "\n" + REPLY):
        env.extraction(key, fields={"problem": PROBLEM}, title="")
    env.created()
    out = env.raw_turn(REPLY, is_test=True, session_vars_in=_draft(message=held_msg, is_test=True),
                       ask_reply=True, update_held=True)
    assert out["status"] == "complete"
    p = env.create_calls[0]
    assert p["raw_transcript"] == held_msg + "\n" + REPLY
    assert p["department"] == "Sales"
    assert p["is_test"] is True
    assert "ideation" not in env.persisted()
