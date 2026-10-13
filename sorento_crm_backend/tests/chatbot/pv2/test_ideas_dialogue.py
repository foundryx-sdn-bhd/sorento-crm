"""PV2-IDEAS red tests, spec section B: `pv2/ideas.py` (`run_idea_turn`).

Pure mapping between the parser-v2 Ask / PendingQuestion contract (CONTRACT-PV2-13oct.md
sections 1, 3, 5) and `handle_capture_turn`. No DB: the capture service is replaced by a fake
that records the kwargs it was called with and returns a scripted result, so these tests prove
the mapping and nothing about ss.

`ask` / `pending` are plain dicts shaped like the contract models (one case also uses
SimpleNamespace objects: the module reads by attribute or key).
"""
from __future__ import annotations

import re
import types
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

# MUST be the first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402, F401

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

MSG = "the price tag should show promo price in red"
TITLES = ["Promo price in red", "Price tag layout"]


def _uid() -> str:
    return str(uuid.uuid4())


def _now_iso(delta: timedelta = timedelta(0)) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #
def make_ask(*, act: str = "new_topic", option_key: str | None = None, free_value: str | None = None,
             idea_count: int | None = 1, adds_to_held_idea: bool = False) -> dict[str, Any]:
    return {
        "act": act,
        "domain": "ideation",
        "switch_back_to": None,
        "entities": [],
        "specs": [],
        "attachment_type": None,
        "period": None,
        "row_pick": None,
        "answer": (
            {"option_key": option_key, "free_value": free_value} if act == "answers_bot_question" else None
        ),
        "cleared": [],
        "report": None,
        "low_stock": None,
        "ideation": (
            {"idea_count": idea_count, "adds_to_held_idea": adds_to_held_idea}
            if idea_count is not None
            else None
        ),
    }


def make_pending(kind: str, options: list[dict] | None = None, *, asked: str = "asked?",
                 asked_turn: int = 3) -> dict[str, Any]:
    return {"kind": kind, "asked": asked, "options": options or [], "asked_turn": asked_turn,
            "misses": 0, "v1_kind": None}


def similar_pending(n: int = 2) -> dict[str, Any]:
    opts = [{"key": str(i), "label": TITLES[i - 1], "payload": {"choice": i}} for i in range(1, n + 1)]
    opts.append({"key": "new", "label": "New idea", "payload": {"choice": "new"}})
    return make_pending("idea_similar", opts)


def multi_pending(original: str = MSG) -> dict[str, Any]:
    return make_pending("idea_multi", [
        {"key": "1", "label": "Save as one idea", "payload": {"choice": "one", "message_text": original}},
        {"key": "2", "label": "Send them one at a time", "payload": {"choice": "separate"}},
    ])


def captured_pointer(*, age: timedelta = timedelta(0), is_test: bool = False) -> dict[str, Any]:
    return {"ideation": {
        "status": "captured",
        "idea_id": _uid(),
        "idea_number": "IDEA-0184",
        "title": "Promo price in red",
        "updated_at": _now_iso(age),
        "is_test": is_test,
    }}


class FakeCapture:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {
            "status": "complete", "reply_text": "Saved.", "session_vars": {},
            "offered_media": [], "language": "en",
        }

    def __call__(self, db, **kw):  # noqa: ANN001
        self.calls.append(kw)
        return self.result

    @property
    def kw(self) -> dict[str, Any]:
        assert len(self.calls) == 1, self.calls
        return self.calls[0]

    # ---- scripted results ----
    def similar_offered(self, n: int = 2, language: str = "en") -> None:
        similar = [{"idea_id": _uid(), "idea_number": f"IDEA-01{i}0", "title": TITLES[i - 1]}
                   for i in range(1, n + 1)]
        lines = ["Similar ideas:"] + [f"{i}. {s['title']} - https://crm.example/ideas/{s['idea_id']}"
                                      for i, s in enumerate(similar, start=1)]
        self.result = {
            "status": "similar_offered", "reply_text": "\n".join(lines),
            "session_vars": {"ideation": {
                "status": "similar_offered", "message_text": MSG, "fields": {"problem": "p"},
                "title": "t", "similar": similar, "intake_ref": _uid(),
                "updated_at": _now_iso(), "is_test": False,
            }},
            "offered_media": [], "language": language,
        }

    def ask_idea(self, status: str = "ask_idea") -> None:
        self.result = {"status": status, "reply_text": "What is the idea?", "session_vars": {},
                       "offered_media": [], "language": "en"}

    def complete(self, status: str = "complete") -> str:
        idea_id = _uid()
        self.result = {
            "status": status, "reply_text": "Saved IDEA-0184", "link": f"https://crm.example/ideas/{idea_id}",
            "session_vars": {"ideation": {
                "status": "captured", "idea_id": idea_id, "idea_number": "IDEA-0184",
                "title": "Promo price in red", "updated_at": _now_iso(), "is_test": False,
            }},
            "offered_media": [], "language": "en",
        }
        return idea_id


@pytest.fixture
def cap(monkeypatch) -> FakeCapture:
    from app.services.chatbot.pv2 import ideas

    fake = FakeCapture()
    monkeypatch.setattr(ideas, "handle_capture_turn", fake)
    return fake


def run(ask, *, pending=None, language="en", message_text=MSG, session_vars=None, turn_no=4,
        is_test=False):
    from app.services.chatbot.pv2 import ideas

    return ideas.run_idea_turn(
        None,
        ask=ask,
        language=language,
        respond_io_id="rio-1",
        message_text=message_text,
        session_vars=session_vars if session_vars is not None else {},
        pending=pending,
        turn_no=turn_no,
        is_test=is_test,
        submitter_name="Alice",
    )


def assert_no_leaks(res) -> None:
    assert " · " not in res.reply_text
    # an id may appear in the reply only inside a CRM link (today's similar-list formatter)
    assert not UUID_RE.search(re.sub(r"https?://\S+", "", res.reply_text))
    p = res.pending
    if p:
        assert not UUID_RE.search(p["asked"])
        for opt in p["options"]:
            assert not UUID_RE.search(opt["key"])
            assert not UUID_RE.search(opt["label"])


# --------------------------------------------------------------------------- #
# module surface                                                              #
# --------------------------------------------------------------------------- #
def test_pending_kinds_constant():
    from app.services.chatbot.pv2 import ideas

    assert ideas.IDEA_PENDING_KINDS == frozenset({"idea_similar", "idea_missing_field", "idea_multi"})


# --------------------------------------------------------------------------- #
# fresh idea -> similar offered                                               #
# --------------------------------------------------------------------------- #
def test_fresh_similar_offered_maps_to_idea_similar_pending(cap):
    cap.similar_offered(2)
    res = run(make_ask(), turn_no=7)

    assert cap.kw["update_held"] is True
    assert cap.kw.get("choice_in") is None
    assert cap.kw["message_text"] == MSG
    assert cap.kw["respond_io_id"] == "rio-1"

    assert res.status == "similar_offered"
    assert res.reply_text == cap.result["reply_text"]
    p = res.pending
    assert p["kind"] == "idea_similar"
    assert [o["key"] for o in p["options"]] == ["1", "2", "new"]
    assert [o["label"] for o in p["options"][:2]] == TITLES
    assert "new" in p["options"][2]["label"].lower()
    assert [o["payload"] for o in p["options"]] == [{"choice": 1}, {"choice": 2}, {"choice": "new"}]
    # the lead line only: the numbered lines carry the ideas' CRM links (ids inside URLs)
    assert p["asked"] == res.reply_text.splitlines()[0]
    assert p["asked_turn"] == 7
    assert p["misses"] == 0
    assert p["v1_kind"] is None
    assert_no_leaks(res)
    # the idea ids stay out of the pending entirely
    for sid in (s["idea_id"] for s in cap.result["session_vars"]["ideation"]["similar"]):
        assert sid not in repr(p)


def test_similar_offered_session_vars_come_from_capture(cap):
    cap.similar_offered(2)
    res = run(make_ask())
    assert res.session_vars == cap.result["session_vars"]
    assert res.idea_draft_id is None


@pytest.mark.parametrize("lang,expected", [("en", "en"), ("ms", "ms"), ("zh", "zh"), ("mixed", "en")])
def test_language_comes_from_capture_result_mixed_is_en(cap, lang, expected):
    cap.similar_offered(2, language=lang)
    res = run(make_ask(), language="en")
    # the capture result's language wins; "mixed" (never a capture language) falls to en
    assert res.language == (lang if lang in ("en", "ms", "zh") else "en")
    assert res.language == expected


def test_new_option_label_is_localized(cap):
    cap.similar_offered(2, language="en")
    en = run(make_ask()).pending["options"][2]["label"]
    cap.similar_offered(2, language="zh")
    zh = run(make_ask(), language="zh").pending["options"][2]["label"]
    assert en and zh and en != zh


def test_three_similar_gives_five_options(cap):
    cap.similar_offered(2)
    cap.result["session_vars"]["ideation"]["similar"].append(
        {"idea_id": _uid(), "idea_number": "IDEA-0990", "title": "Third idea"})
    res = run(make_ask())
    assert [o["key"] for o in res.pending["options"]] == ["1", "2", "3", "new"]
    assert res.pending["options"][2]["label"] == "Third idea"
    assert res.pending["options"][2]["payload"] == {"choice": 3}


# --------------------------------------------------------------------------- #
# answers on idea_similar                                                     #
# --------------------------------------------------------------------------- #
def test_answer_option_2_passes_choice_in_2(cap):
    cap.complete("similar_picked")
    res = run(make_ask(act="answers_bot_question", option_key="2"), pending=similar_pending(),
              message_text="yang kedua")
    assert cap.kw["choice_in"] == 2
    assert isinstance(cap.kw["choice_in"], int) and not isinstance(cap.kw["choice_in"], bool)
    assert cap.kw["update_held"] is True
    assert res.status == "similar_picked"
    assert res.pending is None


def test_answer_option_new_passes_choice_in_new(cap):
    cap.complete()
    run(make_ask(act="answers_bot_question", option_key="new"), pending=similar_pending())
    assert cap.kw["choice_in"] == "new"
    assert cap.kw["update_held"] is True


def test_answer_option_1_passes_choice_in_1(cap):
    cap.complete("similar_picked")
    run(make_ask(act="answers_bot_question", option_key="1"), pending=similar_pending())
    assert cap.kw["choice_in"] == 1


def test_answer_key_not_an_option_returns_none_and_no_capture(cap):
    res = run(make_ask(act="answers_bot_question", option_key="3"), pending=similar_pending(2))
    assert res is None
    assert cap.calls == []


def test_answer_key_none_on_idea_similar_returns_none(cap):
    res = run(make_ask(act="answers_bot_question", option_key=None, free_value="second"),
              pending=similar_pending())
    assert res is None
    assert cap.calls == []


def test_answer_key_is_exact_not_normalised(cap):
    for bad in ("NEW", " 2", "2 ", "second"):
        assert run(make_ask(act="answers_bot_question", option_key=bad), pending=similar_pending()) is None
    assert cap.calls == []


def test_answer_without_pending_is_a_fresh_message(cap):
    cap.similar_offered(2)
    res = run(make_ask(act="answers_bot_question", option_key="2"), pending=None)
    assert res is not None
    assert cap.kw.get("choice_in") is None
    assert cap.kw["update_held"] is True


# --------------------------------------------------------------------------- #
# idea_missing_field                                                          #
# --------------------------------------------------------------------------- #
def test_missing_field_answer_updates_held(cap):
    cap.complete()
    reply = "the customers keep complaining about the red sticker"
    res = run(make_ask(act="answers_bot_question", option_key=None, free_value=reply),
              pending=make_pending("idea_missing_field"), message_text=reply)
    assert cap.kw["ask_reply"] is True
    assert cap.kw["update_held"] is True
    assert cap.kw["message_text"] == reply
    assert cap.kw.get("choice_in") is None
    assert res.status == "complete"


def test_capture_ask_idea_maps_to_missing_field_pending_with_no_options(cap):
    cap.ask_idea()
    res = run(make_ask(), turn_no=9)
    p = res.pending
    assert p["kind"] == "idea_missing_field"
    assert p["options"] == []
    assert p["asked"] == res.reply_text == "What is the idea?"
    assert p["asked_turn"] == 9
    assert p["misses"] == 0
    assert p["v1_kind"] is None
    assert res.idea_draft_id is None


def test_missing_field_reply_that_gives_up_has_no_pending(cap):
    cap.ask_idea("ask_idea_gave_up")
    res = run(make_ask(act="answers_bot_question"), pending=make_pending("idea_missing_field"))
    assert res.status == "ask_idea_gave_up"
    assert res.pending is None


# --------------------------------------------------------------------------- #
# complete / draft id                                                         #
# --------------------------------------------------------------------------- #
def test_complete_sets_idea_draft_id_and_link_and_no_pending(cap):
    idea_id = cap.complete()
    res = run(make_ask())
    assert res.status == "complete"
    assert res.idea_draft_id == idea_id
    assert res.pending is None
    assert res.link == cap.result["link"]
    assert res.language == "en"


def test_similar_picked_sets_idea_draft_id(cap):
    idea_id = cap.complete("similar_picked")
    res = run(make_ask(act="answers_bot_question", option_key="1"), pending=similar_pending())
    assert res.idea_draft_id == idea_id
    assert res.pending is None


@pytest.mark.parametrize("status", ["error", "config_error", "no_access", "unconfigured"])
def test_other_statuses_have_no_pending_and_no_draft(cap, status):
    cap.result = {"status": status, "reply_text": "nope", "session_vars": {}, "offered_media": [],
                  "language": "en"}
    res = run(make_ask())
    assert res.status == status
    assert res.pending is None
    assert res.idea_draft_id is None


# --------------------------------------------------------------------------- #
# idea_multi                                                                  #
# --------------------------------------------------------------------------- #
def test_two_ideas_offers_multi_without_calling_capture(cap):
    res = run(make_ask(idea_count=2), message_text=MSG, turn_no=5)
    assert cap.calls == []
    assert res.status == "idea_multi_offered"
    p = res.pending
    assert p["kind"] == "idea_multi"
    # keys are the numbers the reply prints, as for idea_similar
    assert [o["key"] for o in p["options"]] == ["1", "2"]
    assert p["options"][0]["payload"]["choice"] == "one"
    assert p["options"][0]["payload"]["message_text"] == MSG
    assert p["options"][1]["payload"]["choice"] == "separate"
    # the asked line only; the options are listed separately
    assert p["asked"] == res.reply_text.splitlines()[0]
    assert "\n" not in p["asked"]
    assert p["asked_turn"] == 5 and p["misses"] == 0 and p["v1_kind"] is None
    lines = [ln.strip() for ln in res.reply_text.splitlines()]
    assert any(ln.startswith("1.") for ln in lines)
    assert any(ln.startswith("2.") for ln in lines)
    assert res.idea_draft_id is None
    assert_no_leaks(res)


def test_three_ideas_is_also_multi(cap):
    res = run(make_ask(idea_count=3))
    assert cap.calls == []
    assert res.pending["kind"] == "idea_multi"


def test_multi_asked_line_is_localized_zh_differs_from_en(cap):
    en = run(make_ask(idea_count=2), language="en")
    zh = run(make_ask(idea_count=2), language="zh")
    ms = run(make_ask(idea_count=2), language="ms")
    assert en.pending["asked"] != zh.pending["asked"]
    assert re.search(r"[一-鿿]", zh.pending["asked"])
    assert not re.search(r"[一-鿿]", en.pending["asked"])
    assert ms.pending["asked"] != en.pending["asked"]
    assert zh.language == "zh" and ms.language == "ms" and en.language == "en"


def test_multi_mixed_language_is_en(cap):
    res = run(make_ask(idea_count=2), language="mixed")
    assert res.language == "en"


def test_multi_answer_one_captures_original_message(cap):
    cap.complete()
    res = run(make_ask(act="answers_bot_question", option_key="1", idea_count=None),
              pending=multi_pending("ORIGINAL two ideas message"), message_text="yes one please")
    assert cap.kw["message_text"] == "ORIGINAL two ideas message"
    assert cap.kw["update_held"] is True
    assert cap.kw.get("choice_in") is None
    assert res.status == "complete"


def test_multi_answer_separate_asks_for_the_first_idea_without_capture(cap):
    res = run(make_ask(act="answers_bot_question", option_key="2", idea_count=None),
              pending=multi_pending(), turn_no=11)
    assert cap.calls == []
    assert res.status == "ask_idea"
    assert res.reply_text
    assert res.pending["kind"] == "idea_missing_field"
    assert res.pending["options"] == []
    assert res.pending["asked_turn"] == 11
    assert res.idea_draft_id is None


def test_multi_answer_unknown_key_returns_none(cap):
    assert run(make_ask(act="answers_bot_question", option_key="both"), pending=multi_pending()) is None
    assert run(make_ask(act="answers_bot_question", option_key=None), pending=multi_pending()) is None
    assert cap.calls == []


# --------------------------------------------------------------------------- #
# adds_to_held_idea                                                           #
# --------------------------------------------------------------------------- #
def test_adds_to_held_with_fresh_captured_pointer_opens_it_without_capture(cap, monkeypatch):
    sv = captured_pointer()
    res = run(make_ask(adds_to_held_idea=True), session_vars=sv)
    assert cap.calls == []
    assert res.status == "held_idea_open"
    assert res.idea_draft_id == sv["ideation"]["idea_id"]
    assert res.reply_text
    assert not UUID_RE.search(res.reply_text.replace("https://crm.example/ideas/" + sv["ideation"]["idea_id"], ""))
    assert res.pending is None


def test_adds_to_held_with_stale_pointer_runs_capture_normally(cap):
    cap.complete()
    res = run(make_ask(adds_to_held_idea=True), session_vars=captured_pointer(age=timedelta(hours=25)))
    assert cap.kw["update_held"] is True
    assert res.status == "complete"


def test_adds_to_held_with_other_is_test_runs_capture(cap):
    cap.complete()
    run(make_ask(adds_to_held_idea=True), session_vars=captured_pointer(is_test=True), is_test=False)
    assert len(cap.calls) == 1


def test_adds_to_held_without_pointer_runs_capture(cap):
    cap.complete()
    run(make_ask(adds_to_held_idea=True), session_vars={})
    assert len(cap.calls) == 1


def test_captured_pointer_without_adds_flag_runs_capture(cap):
    cap.complete()
    run(make_ask(adds_to_held_idea=False), session_vars=captured_pointer())
    assert len(cap.calls) == 1


def test_adds_to_held_non_captured_pointer_runs_capture(cap):
    cap.ask_idea()
    sv = {"ideation": {"status": "draft_held", "message_text": "x", "fields": {}, "title": "",
                       "updated_at": _now_iso(), "is_test": False}}
    run(make_ask(adds_to_held_idea=True), session_vars=sv)
    assert len(cap.calls) == 1


# --------------------------------------------------------------------------- #
# pydantic-like objects                                                       #
# --------------------------------------------------------------------------- #
def test_accepts_attribute_objects(cap):
    cap.complete("similar_picked")
    ask = types.SimpleNamespace(
        act="answers_bot_question",
        domain="ideation",
        switch_back_to=None,
        entities=[],
        specs=[],
        attachment_type=None,
        period=None,
        row_pick=None,
        answer=types.SimpleNamespace(option_key="2", free_value=None),
        cleared=[],
        report=None,
        low_stock=None,
        ideation=types.SimpleNamespace(idea_count=1, adds_to_held_idea=False),
    )
    options = [
        types.SimpleNamespace(key="1", label="A", payload={"choice": 1}),
        types.SimpleNamespace(key="2", label="B", payload={"choice": 2}),
        types.SimpleNamespace(key="new", label="New idea", payload={"choice": "new"}),
    ]
    pending = types.SimpleNamespace(kind="idea_similar", asked="q", options=options, asked_turn=1,
                                    misses=0, v1_kind=None)
    res = run(ask, pending=pending)
    assert cap.kw["choice_in"] == 2
    assert res.status == "similar_picked"


def test_attribute_ask_without_ideation_group_runs_capture(cap):
    cap.complete()
    ask = types.SimpleNamespace(act="new_topic", answer=None, ideation=None)
    run(ask)
    assert len(cap.calls) == 1


# --------------------------------------------------------------------------- #
# leak guard across every produced pending                                    #
# --------------------------------------------------------------------------- #
def test_leak_guard_over_every_pending(cap):
    produced = []
    cap.similar_offered(2)
    produced.append(run(make_ask()))
    cap.ask_idea()
    produced.append(run(make_ask()))
    for lang in ("en", "ms", "zh"):
        produced.append(run(make_ask(idea_count=2), language=lang))
    produced.append(run(make_ask(act="answers_bot_question", option_key="2", idea_count=None),
                        pending=multi_pending()))
    pendings = [r for r in produced if r.pending]
    assert len(pendings) == len(produced)
    for res in produced:
        assert_no_leaks(res)
