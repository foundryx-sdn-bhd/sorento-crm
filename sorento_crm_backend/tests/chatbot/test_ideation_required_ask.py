"""IDEATION-CAPTURE slice H1: the ideate lane asks for the idea through the shared helper.

`documentation/plans/ideation/PLAN-ideation-capture-02oct.md`, section 4a step 4 ("Helper
relayed 2 Oct", "Crew answers 2 Oct", "Vague reply never loops silently"):

* the lane registers `AskType("ideation")` in `required_fields.ASKS` at import: one required
  field `problem`, `allow_all=False`, rerouting back to the ideate lane;
* the core turn DECIDES (`status == "ask_idea"`); the lane then opens the slot on its result
  (`required_ask`) and shows the TOOL's localised `reply_text`, never the helper's English
  question; any other status carries no slot;
* on the answering turn the parse output carries `required_ask` + `required_ask_reply`, and
  `build_arguments` sends `ask_reply: True` with `message_text` = the reply text;
* "want to submit idea" twice ends in a clear message (`ask_idea_gave_up`) and no third ask.

The tool is faked at the `ideate.call_ideation_tool` seam; no MCP. Placeholder data only.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text

from app.services.chatbot import engine as engine_mod
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures reused by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)
from tests.chatbot.test_s3_canned_and_ideate import _seed_completed_lanes

ASK_REPLY = "Zzt localised ask-back: what would you like changed?"
GAVE_UP_REPLY = "Zzt localised close: send your idea in one message."
DONE_REPLY = "Idea IDEA-0001 recorded."


def _slot() -> dict[str, Any]:
    return {"ask": "ideation", "values": {}, "asking": "problem", "options": [], "misses": 0, "extras": {}}


def _ctx(parse_output: dict[str, Any] | None = None, text_: str = "want to submit idea") -> dict[str, Any]:
    return {
        "parse": {"output": parse_output or {}},
        "session": {"session_vars": {}},
        "text": {"message": {"message": {"text": text_}}},
        "contact": {"id": 4242, "firstName": "ZZT"},
    }


def _fake_tool(monkeypatch, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.services.chatbot.lanes import ideate as ideate_mod

    calls: list[dict[str, Any]] = []

    def _call(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return dict(results[min(len(calls), len(results)) - 1])

    monkeypatch.setattr(ideate_mod, "call_ideation_tool", _call)
    return calls


def _result(status: str, reply: str) -> dict[str, Any]:
    return {"status": status, "reply_text": reply, "link": None, "session_vars": {"ideation": None}}


# --------------------------------------------------------------------------- #
# 1. Registration
# --------------------------------------------------------------------------- #


class TestRegistered:
    def test_the_ideation_ask_is_registered_when_the_lane_is_imported(self) -> None:
        from app.services.chatbot import required_fields
        from app.services.chatbot.lanes import ideate  # noqa: F401

        assert "ideation" in required_fields.ASKS

    def test_it_has_one_required_field_problem_that_does_not_take_all(self) -> None:
        from app.services.chatbot import required_fields
        from app.services.chatbot.lanes import ideate  # noqa: F401

        ask = required_fields.ASKS["ideation"]
        assert [f.name for f in ask.fields] == ["problem"]
        (spec,) = ask.fields
        assert spec.required is True
        assert spec.allow_all is False

    def test_it_reroutes_back_to_the_ideate_lane(self) -> None:
        from app.services.chatbot import required_fields
        from app.services.chatbot.lanes import ideate  # noqa: F401

        reroute = required_fields.ASKS["ideation"].reroute
        assert reroute.get("intent_hint") == "submit_idea"
        assert reroute.get("domain_hint") == "ideate"
        assert reroute.get("message_type") == "business_query"

    def test_a_short_reply_to_the_open_slot_is_rerouted_to_ideate(self) -> None:
        from app.services.chatbot import required_fields
        from app.services.chatbot.lanes import ideate  # noqa: F401

        parsed = {"message_type": "business_query", "domain_hint": "master_products",
                  "intent_hint": "check_product", "entities": [{"raw": "basin"}]}
        verdict, rule = required_fields.reply_verdict(parsed, _slot(), "leaky basin")
        assert rule == "required_ask_answer"
        assert verdict["intent_hint"] == "submit_idea" and verdict["domain_hint"] == "ideate"
        assert verdict["required_ask"] == _slot() and verdict["required_ask_reply"] == "leaky basin"


# --------------------------------------------------------------------------- #
# 2 + 3. The lane opens (or does not open) the slot
# --------------------------------------------------------------------------- #


class TestLaneSlot:
    def test_ask_idea_carries_the_slot_and_the_tools_own_reply(self, monkeypatch) -> None:
        from app.services.chatbot.lanes import ideate

        _fake_tool(monkeypatch, [_result("ask_idea", ASK_REPLY)])
        out = ideate.run(_ctx(), {})
        slot = out["required_ask"]
        assert slot["ask"] == "ideation" and slot["asking"] == "problem"
        assert out["item"]["response"] == ASK_REPLY

    @pytest.mark.parametrize(
        "status",
        ["complete", "similar_offered", "no_access", "ask_idea_gave_up", "error", "config_error"],
    )
    def test_any_other_status_carries_no_slot(self, monkeypatch, status) -> None:
        from app.services.chatbot.lanes import ideate

        _fake_tool(monkeypatch, [_result(status, "Zzt reply")])
        out = ideate.run(_ctx(), {})
        assert out.get("required_ask") is None


# --------------------------------------------------------------------------- #
# 4. The answering turn's arguments
# --------------------------------------------------------------------------- #


class TestBuildArguments:
    def test_an_answering_turn_sends_ask_reply_and_the_reply_text(self) -> None:
        from app.services.chatbot.lanes import ideate

        parse = {"required_ask": _slot(), "required_ask_reply": "the tap handle is too stiff"}
        body = ideate.build_arguments(_ctx(parse, text_="ignored original text"))
        assert body["ask_reply"] is True
        assert body["message_text"] == "the tap handle is too stiff"

    def test_a_normal_turn_has_no_ask_reply(self) -> None:
        from app.services.chatbot.lanes import ideate

        body = ideate.build_arguments(_ctx({}, text_="want to submit idea"))
        assert "ask_reply" not in body
        assert body["message_text"] == "want to submit idea"

    def test_a_slot_of_another_ask_does_not_set_ask_reply(self) -> None:
        from app.services.chatbot.lanes import ideate

        other = {**_slot(), "ask": "low_stock_report"}
        body = ideate.build_arguments(_ctx({"required_ask": other, "required_ask_reply": "x"}))
        assert "ask_reply" not in body


# --------------------------------------------------------------------------- #
# 5 + 6. Through the engine: the slot survives into state, the next turn answers it
# --------------------------------------------------------------------------- #

IDEATE_PARSE = dict(message_type="business_query", intent_hint="submit_idea", domain_hint="ideate")


def _say(session_factory, stub_parser, parse: dict[str, Any], body: str, message_id: str):
    stub_parser(_parser_output(**parse))
    envelope = _envelope()
    envelope.message["message"]["message"]["text"] = body
    envelope.message["message"]["messageId"] = message_id
    return engine_mod.run_turn(envelope, session_factory=session_factory)


def _focus_slot(session_factory) -> Any:
    raw = session_factory().execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
        {"c": str(CONTACT_ID)},
    ).scalar()
    raw = json.loads(raw) if isinstance(raw, str) else raw
    return ((raw or {}).get("focus") or {}).get("required_ask")


class TestThroughTheEngine:
    def test_the_ask_idea_slot_is_kept_in_state_for_the_next_message(
        self, session_factory, seeded, system_settings_row, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Engine plumbing: engine.py collects `required_ask` off `envelopes` only on the
        business-lane path (~5723); an ideate turn completes in `_complete_canned_lane`."""
        _seed_completed_lanes(session_factory, system_settings_row)
        stub_access()
        _fake_tool(monkeypatch, [_result("ask_idea", ASK_REPLY)])

        result = _say(session_factory, stub_parser, IDEATE_PARSE, "want to submit idea", "ZZT-ri-1")

        assert result.reply["text"] == ASK_REPLY
        slot = _focus_slot(session_factory)
        assert isinstance(slot, dict) and slot["ask"] == "ideation" and slot["asking"] == "problem"

    def test_a_vague_second_message_ends_with_the_give_up_and_no_third_ask(
        self, session_factory, seeded, system_settings_row, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_completed_lanes(session_factory, system_settings_row)
        stub_access()
        calls = _fake_tool(
            monkeypatch,
            [_result("ask_idea", ASK_REPLY), _result("ask_idea_gave_up", GAVE_UP_REPLY)],
        )

        _say(session_factory, stub_parser, IDEATE_PARSE, "want to submit idea", "ZZT-ri-2a")
        result = _say(session_factory, stub_parser, IDEATE_PARSE, "ok i want to submit one", "ZZT-ri-2b")

        assert len(calls) == 2
        assert calls[0].get("ask_reply") is None
        assert calls[1]["ask_reply"] is True
        assert calls[1]["message_text"] == "ok i want to submit one"
        assert result.reply["text"] == GAVE_UP_REPLY
        assert result.reply["ideate_status"] == "ask_idea_gave_up"
        assert _focus_slot(session_factory) is None

    def test_a_real_idea_in_the_reply_runs_the_normal_flow_and_closes_the_slot(
        self, session_factory, seeded, system_settings_row, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_completed_lanes(session_factory, system_settings_row)
        stub_access()
        calls = _fake_tool(
            monkeypatch, [_result("ask_idea", ASK_REPLY), _result("complete", DONE_REPLY)]
        )

        _say(session_factory, stub_parser, IDEATE_PARSE, "want to submit idea", "ZZT-ri-3a")
        # A two-word reply the parser reads as a product lookup: the slot reroutes it to ideate.
        result = _say(
            session_factory,
            stub_parser,
            dict(message_type="business_query", intent_hint="check_product", domain_hint="master_products"),
            "leaky basin",
            "ZZT-ri-3b",
        )

        assert len(calls) == 2
        assert calls[1]["ask_reply"] is True
        assert calls[1]["message_text"] == "leaky basin"
        assert result.reply["text"] == DONE_REPLY
        assert result.reply["ideate_status"] == "complete"
        assert _focus_slot(session_factory) is None


# --------------------------------------------------------------------------- #
# 7. The slot never goes stale
# --------------------------------------------------------------------------- #

CASUAL_PARSE = dict(message_type="casual", domain_hint=None, intent_hint=None, correction=False, entities=[])


class TestSlotNeverGoesStale:
    def test_a_turn_routed_away_from_ideate_leaves_no_slot_and_no_tool_call(
        self, session_factory, seeded, system_settings_row, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_completed_lanes(session_factory, system_settings_row)
        stub_access()
        calls = _fake_tool(monkeypatch, [_result("ask_idea", ASK_REPLY)])

        _say(session_factory, stub_parser, IDEATE_PARSE, "want to submit idea", "ZZT-stale-1a")
        assert _focus_slot(session_factory) is not None
        assert len(calls) == 1

        # Longer than three words, so the shared helper does not read it as the answer.
        _say(session_factory, stub_parser, CASUAL_PARSE, "thanks that is all for today", "ZZT-stale-1b")

        assert len(calls) == 1
        assert _focus_slot(session_factory) is None

    def test_a_question_reply_drops_the_slot(
        self, session_factory, seeded, system_settings_row, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_completed_lanes(session_factory, system_settings_row)
        stub_access()
        calls = _fake_tool(monkeypatch, [_result("ask_idea", ASK_REPLY), _result("similar_offered", "Zzt reply")])

        _say(session_factory, stub_parser, IDEATE_PARSE, "want to submit idea", "ZZT-stale-2a")
        assert _focus_slot(session_factory) is not None

        # A "?" is never an answer (required_fields.reply_verdict), even though the parser
        # still reads it as the ideate ask: the tool runs, but not as an ask reply.
        _say(session_factory, stub_parser, IDEATE_PARSE, "why?", "ZZT-stale-2b")

        assert len(calls) == 2
        assert calls[1].get("ask_reply") is None
        assert _focus_slot(session_factory) is None

    def test_a_status_that_creates_an_idea_closes_the_slot_for_the_next_message(
        self, session_factory, seeded, system_settings_row, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_completed_lanes(session_factory, system_settings_row)
        stub_access()
        calls = _fake_tool(
            monkeypatch,
            [_result("ask_idea", ASK_REPLY), _result("complete", DONE_REPLY), _result("ask_idea", ASK_REPLY)],
        )

        _say(session_factory, stub_parser, IDEATE_PARSE, "want to submit idea", "ZZT-stale-3a")
        assert _focus_slot(session_factory) is not None
        _say(session_factory, stub_parser, IDEATE_PARSE, "the tap handle is too stiff", "ZZT-stale-3b")
        assert calls[1]["ask_reply"] is True
        assert _focus_slot(session_factory) is None

        _say(session_factory, stub_parser, IDEATE_PARSE, "want to submit another idea", "ZZT-stale-3c")

        assert len(calls) == 3
        assert calls[2].get("ask_reply") is None
