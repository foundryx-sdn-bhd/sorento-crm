"""PV2-CORE: the ONE engine hook (CONTRACT-PV2-13oct.md section 6).

PV2-CARRY's `pv2.turn.run_turn_v2` is replaced by a fake module so this lane tests only
the seam: flag off = v1 untouched; flag on = the v2 verdict runs the normal stages, a
direct reply closes the turn, a v2 parse failure is a failed `understood`, and the pv2
state lands in `session_vars["pv2"]` (live) or `session_patch` (dry run).
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import text

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.pv2 import call as pv2_call
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    _turn_row,
    seeded,
    stub_access,
    stub_parser,
)

TURN_MOD = "app.services.chatbot.pv2.turn"


@dataclass
class FakeState:
    turn_no: int = 1
    held: dict = field(default_factory=lambda: {"domain": "inventory"})

    def model_dump(self, mode: str = "python") -> dict:
        return {"v": 1, "turn_no": self.turn_no, "held": self.held}


@dataclass
class FakeResult:
    verdict: dict | None = None
    focus: Any = None
    direct_reply: str | None = None
    state: Any = field(default_factory=FakeState)
    usage: dict = field(default_factory=lambda: {"total_tokens": 42, "prompt_tokens": 40, "completion_tokens": 2})
    trace: dict = field(default_factory=lambda: {"index_sha": "abc12345", "version": 3, "acts": ["new_topic"]})
    branch_kind: str | None = None
    quick_replies: str | None = None


@pytest.fixture()
def fake_turn(monkeypatch):
    calls: list = []

    def install(result=None, *, error: Exception | None = None):
        mod = types.ModuleType(TURN_MOD)

        def run_turn_v2(ctx):
            calls.append(ctx)
            if error is not None:
                raise error
            return result

        mod.run_turn_v2 = run_turn_v2
        monkeypatch.setitem(sys.modules, TURN_MOD, mod)
        return calls

    return install


def _set_contact_mode(session_factory, mode):
    db = session_factory()
    db.execute(
        text("update respond_contacts set chatbot_parser_mode = :m where respond_io_id = :c"),
        {"m": mode, "c": str(CONTACT_ID)},
    )
    db.commit()


def _session_vars(session_factory) -> dict:
    return session_factory().execute(
        text("select session_vars from respond_contacts where respond_io_id = :c"),
        {"c": str(CONTACT_ID)},
    ).scalar()


def _events(trace, kind):
    return [r for r in trace if r.get("kind") == kind]


def test_flag_off_never_calls_v2(session_factory, seeded, stub_parser, stub_access, fake_turn):
    calls = fake_turn(FakeResult(direct_reply="should not happen"))
    stub_parser()
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert calls == []
    assert result.branch_kind == "business_query"
    assert "pv2" not in (_session_vars(session_factory) or {})


def test_v2_direct_reply_closes_turn(session_factory, seeded, stub_parser, stub_access, fake_turn):
    calls = fake_turn(FakeResult(direct_reply="*Which 600 mm?*\n1. Length\n2. Height", branch_kind="clarify_menu"))
    stub_parser(error=AssertionError("v1 parser must not run on a v2 turn"))
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert len(calls) == 1
    ctx = calls[0]
    assert ctx.text == "price for SRTWC8517" and ctx.contact_respond_id == str(CONTACT_ID)
    assert ctx.dry_run is False
    assert result.status == "done" and result.branch_kind == "clarify_menu"
    assert result.reply["text"].startswith("*Which 600 mm?*")
    assert [a["kind"] for a in result.actions if a["kind"] == "send_message"] == ["send_message"]
    row = _turn_row(session_factory, result.turn_id)
    assert row.status == "done" and row.response["reply"]["text"].startswith("*Which")
    pv2_events = _events(row.trace, "pv2_turn")
    assert pv2_events and pv2_events[0]["index_sha"] == "abc12345"
    assert _session_vars(session_factory)["pv2"] == {"v": 1, "turn_no": 1, "held": {"domain": "inventory"}}


def test_v2_verdict_runs_normal_stages(session_factory, seeded, stub_parser, stub_access, fake_turn):
    fake_turn(FakeResult(verdict=_parser_output(asks=None, topic_reset=None, user_goal="from pv2 bridge")))
    stub_parser(error=AssertionError("v1 parser must not run on a v2 turn"))
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert result.branch_kind == "business_query"
    assert result.ctx["parse"]["output"]["user_goal"] == "from pv2 bridge"
    assert _session_vars(session_factory)["pv2"]["held"] == {"domain": "inventory"}


def test_v2_bridge_verdict_missing_keys_fails_understood(
    session_factory, seeded, stub_parser, stub_access, fake_turn
):
    fake_turn(FakeResult(verdict={"message_type": "business_query"}))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert result.status == "failed" and result.stage == "understood"


def test_v2_parse_error_is_failed_understood(session_factory, seeded, stub_parser, stub_access, fake_turn):
    fake_turn(error=pv2_call.ParserV2Error("parser v2 returned non-JSON content", usage={"total_tokens": 9}))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert result.status == "failed" and result.stage == "understood"
    assert "pv2" not in (_session_vars(session_factory) or {})


def test_v2_rate_limited_uses_rate_limited_reply(session_factory, seeded, stub_parser, stub_access, fake_turn):
    from app.services.chatbot import llm_call

    fake_turn(error=pv2_call.ParserV2Error("slow", rate_limited=True))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert result.reply["text"] == llm_call.RATE_LIMITED_REPLY


def test_v2_without_carry_falls_back_to_v1(session_factory, seeded, stub_parser, stub_access, monkeypatch):
    monkeypatch.setitem(sys.modules, TURN_MOD, None)  # import of pv2.turn raises
    from app.services.chatbot.pv2 import hook

    monkeypatch.setattr(hook, "available", lambda: False)
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert result.branch_kind == "business_query"
    row = _turn_row(session_factory, result.turn_id)
    assert _events(row.trace, "pv2_turn")[0]["parser_mode"] == "v1"


def test_dry_run_hands_state_back_in_session_patch(
    session_factory, seeded, stub_parser, stub_access, fake_turn
):
    fake_turn(FakeResult(direct_reply="Hi! How can I help?", branch_kind="low_signal"))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
    assert result.is_test is True
    assert result.session_patch["pv2"]["held"] == {"domain": "inventory"}
    assert "pv2" not in (_session_vars(session_factory) or {})


def test_harness_mock_wins_over_v2(session_factory, seeded, stub_access, fake_turn, monkeypatch):
    from app.services.chatbot.head import parser as parser_mod

    calls = fake_turn(FakeResult(direct_reply="no"))
    monkeypatch.setattr(
        parser_mod, "resolve_config",
        lambda db, *, current_date, override_version_id=None: parser_mod.ParserConfig(
            system_prompt="s", prompt_version=1, provider="openai", model="m", api_key="k"),
    )
    stub_access()
    _set_contact_mode(session_factory, "v2")
    engine_mod.run_turn(
        _envelope(is_test=True, mock_reformulator_output=_parser_output(asks=None, topic_reset=None)),
        session_factory=session_factory,
    )
    assert calls == []


# --------------------------------------------------------------------------- review round 1


def _usage_rows(session_factory) -> int:
    return session_factory().execute(text("select count(*) from ai_assistant_usage_logs")).scalar()


def test_v2_parse_error_logs_the_billed_usage(session_factory, seeded, stub_parser, stub_access, fake_turn):
    before = _usage_rows(session_factory)
    fake_turn(error=pv2_call.ParserV2Error("non-JSON", usage={"total_tokens": 9, "prompt_tokens": 8,
                                                              "completion_tokens": 1, "model": "m", "provider": "openai"}))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert _usage_rows(session_factory) == before + 1


def test_unexpected_v2_error_is_failed_understood_not_a_crash(
    session_factory, seeded, stub_parser, stub_access, fake_turn
):
    fake_turn(error=RuntimeError("resolver blew up"))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert result.status == "failed" and result.stage == "understood"


def test_failed_bridge_turn_does_not_move_pv2_state(
    session_factory, seeded, stub_parser, stub_access, fake_turn
):
    fake_turn(FakeResult(verdict={"message_type": "business_query"}))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert "pv2" not in (_session_vars(session_factory) or {})


def test_v2_understood_records_the_v2_prompt_version(
    session_factory, seeded, stub_parser, stub_access, fake_turn
):
    fake_turn(FakeResult(verdict=_parser_output(asks=None, topic_reset=None)))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    row = _turn_row(session_factory, result.turn_id)
    understood = [r for r in row.trace if r.get("stage") == "understood"][0]
    assert understood["facts"]["prompt_version"] == 3


def test_dry_run_bridged_turn_patch_keeps_v1_keys_and_pv2(
    session_factory, seeded, stub_parser, stub_access, fake_turn
):
    fake_turn(FakeResult(verdict=_parser_output(asks=None, topic_reset=None)))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
    assert "focus" in result.session_patch and "open_question" in result.session_patch
    assert result.session_patch["pv2"]["held"] == {"domain": "inventory"}


def test_dry_run_direct_reply_patch_keeps_the_v1_keys_it_started_with(
    session_factory, seeded, stub_parser, stub_access, fake_turn
):
    calls = fake_turn(FakeResult(direct_reply="Which one?", branch_kind="clarify_menu"))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    carried = {"focus": None, "open_question": None, "ideation": None, "access_levels": [],
               "contains_flyer": False, "reply_language": "zh", "pv2": {"v": 1, "turn_no": 4}}
    result = engine_mod.run_turn(
        _envelope(is_test=True, previous_conversation_state=carried), session_factory=session_factory
    )
    assert result.session_patch["reply_language"] == "zh"
    assert result.session_patch["pv2"]["turn_no"] == 1
    # the harness's pv2 state reached run_turn_v2 (console multi-turn carry)
    assert calls[0].session_block["session_vars"]["pv2"] == {"v": 1, "turn_no": 4}


def test_v1_tail_write_keeps_stored_pv2(session_factory, seeded):
    from app.services.chatbot.turn import tail

    db = session_factory()
    db.execute(
        text("update respond_contacts set session_vars = cast(:s as jsonb) where respond_io_id = :c"),
        {"s": '{"focus": null, "pv2": {"v": 1, "turn_no": 2}}', "c": str(CONTACT_ID)},
    )
    db.commit()
    tail.write_merged(db, respond_io_id=str(CONTACT_ID), payload={"focus": None, "open_question": None},
                      base=None, turn_id=None)
    assert _session_vars(session_factory)["pv2"] == {"v": 1, "turn_no": 2}
