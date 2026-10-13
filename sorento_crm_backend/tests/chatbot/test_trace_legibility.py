"""AC-007: every trace record is plain language, for every `branch_kind`.

`test_engine.py::TestHappyPath.test_the_trace_is_sentences_not_json` proves this for the
one branch kind its happy path exercises (`business_query`). This file drives `run_turn`
through the remaining declared kinds - the AC's own wording is "any stage of a turn", not
"the common one" - and adds the "is a sentence" property `test_engine.py` did not check: a
summary or `why` a machine assembled by string-joining without a separator is not a
sentence even though it contains no `{`/`[`, so every record here must also contain a
space.

Retired 16 Sep 2026 (AC-1592, coordinator ruling - the S3/S6 rearch changed what these
`branch_kind`s mean or how they are reached, so the OLD scenario setup here no longer
exercises the branch it claims to):

- `test_escalate_offer`, `test_out_of_scope` - both routed through the now-retired
  `output_exchange` module (AC-1594). Since the S3 rearch, `branch_kind == "out_of_scope"`
  is reached via the escalation-arm team-pick offer (contract 106/108) whose acknowledgement
  lives in `send_message`/`add_comment` actions, never `reply["text"]` - replacement
  coverage (including trace-record shape) is `test_rearch_s3_team_pick_and_866.py`
  (`TestPort866AsRunTurnCases`).
- `test_offer_hold` - asserted the legacy flat `session_vars["selection_context"]` /
  `routing_roster_plan"]` keys (AC-1504/1521 nested-shape retirement); the S6/S0 rearch
  moved this state under `session_vars["variables"]["focus"]`. No standalone
  trace-legibility replacement was named for `offer_hold` specifically - flagged, not
  ported, per the coordinator's instruction to retire per the verdict list as given.
- `test_stock_denied`, `test_demand_qty` - the S6 ruling replaced the direct
  `custom_fields[].name == "is_allowed_stock"` injection this file used with the real
  stock-visibility gate; replacement coverage is `test_rearch_s6_stock_allowed.py`.

`test_ideate` is NOT retired - its failure was an ENV gap (the autouse MCP-`call_tool`
guard in `conftest.py` forbids an unstubbed `crm_ideation_turn` call), fixed below by
stubbing `ideate_mod.call_ideation_tool` the same way `test_s3_canned_and_ideate.py`'s
`test_ideate_branch_calls_mcp_tool` does.

Nothing here reaches an LLM, n8n, respond.io or the MCP server - same seam as
`test_engine.py`.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import BRANCH_KINDS
from tests.chatbot.test_engine import (  # noqa: F401 - re-exported fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)


def _assert_trace_is_legible(trace: list[dict[str, Any]]) -> None:
    """Every STAGE RECORD in the persisted array is a sentence.

    `chatbot.turns.trace` carries two kinds of entry since growth r1 A9. A stage record
    (`TurnTrace.record`) has a `stage` and no `kind`, and is what this AC is about: an
    operator reads it. An EVENT (`TurnTrace.add` - a tool call, a cross-domain probe, the
    field reveals) has a flat `kind` and is deliberately technical detail: raw args and a
    raw MCP envelope, which `trace_detail.py` renders in its own sections and which no
    honest `summary` could be written for. Demanding prose of one would only ever produce
    a fake sentence, so the filter is the `kind` key itself.

    The stage list is still asserted non-empty, so "skip everything" is not a way to pass.
    """
    records = [entry for entry in trace if entry.get("kind") is None]
    assert records, "no trace records were written at all"
    for record in records:
        assert "raw" in record, record
        summary, why = record["summary"], record["why"]
        assert summary and why, record
        assert "{" not in summary and "[" not in summary, summary
        assert "{" not in why and "[" not in why, why
        # A sentence, not a bare token or a string-joined-without-a-separator blob.
        assert " " in summary, f"not a sentence: {summary!r}"
        assert " " in why, f"not a sentence: {why!r}"


def _run(session_factory, envelope) -> Any:
    result = engine_mod.run_turn(envelope, session_factory=session_factory)
    row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
    return result, row.trace


def _set_session_vars(session_factory, variables: dict[str, Any]) -> None:
    db = session_factory()
    db.execute(
        text(
            "UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) "
            "WHERE respond_io_id = :c"
        ),
        {"c": str(CONTACT_ID), "sv": json.dumps({"variables": variables})},
    )
    db.commit()


class TestTraceLegibilityAcrossBranchKinds:
    """One test per `contracts.BRANCH_KINDS` entry, each driving a real `run_turn` call."""

    def test_access_denied(self, session_factory, seeded, stub_parser, stub_access):
        stub_parser(_parser_output())
        stub_access(allowed=False, decision="deny_no_access")
        result, trace = _run(session_factory, _envelope())
        assert result.branch_kind == "access_denied"
        _assert_trace_is_legible(trace)

    def test_ideate(self, session_factory, seeded, stub_parser, stub_access, monkeypatch):
        from app.services.chatbot.lanes import ideate as ideate_mod

        def _fake_call(**kwargs):
            return {
                "status": "complete",
                "reply_text": "Idea IDEA-1 recorded. Thank you!",
                "link": None,
                "session_vars": {"ideation": None},
            }

        monkeypatch.setattr(ideate_mod, "call_ideation_tool", _fake_call)
        stub_parser(_parser_output(domain_hint="ideate", intent_hint="submit_idea"))
        stub_access()
        result, trace = _run(session_factory, _envelope())
        assert result.branch_kind == "ideate"
        _assert_trace_is_legible(trace)

    def test_escalation_declined(self, session_factory, seeded, stub_parser, stub_access):
        stub_parser(
            _parser_output(
                escalation={"is_escalation_confirmation": False, "escalation_declined": True}
            )
        )
        stub_access()
        result, trace = _run(session_factory, _envelope())
        assert result.branch_kind == "escalation_declined"
        _assert_trace_is_legible(trace)

    def test_check_promotion(self, session_factory, seeded, stub_parser, stub_access):
        stub_parser(_parser_output(intent_hint="check_promotion", domain_hint="promotion"))
        stub_access()
        result, trace = _run(session_factory, _envelope())
        assert result.branch_kind == "check_promotion"
        _assert_trace_is_legible(trace)

    def test_low_signal(self, session_factory, seeded, stub_parser, stub_access):
        stub_parser(_parser_output(message_type="casual", domain_hint=None, intent_hint=None))
        stub_access()
        result, trace = _run(session_factory, _envelope())
        assert result.branch_kind == "low_signal"
        _assert_trace_is_legible(trace)

    def test_clarify_menu(self, session_factory, seeded, stub_parser, stub_access):
        """Same normalisation caveat as `test_escalate_offer` above."""
        stub_parser(
            _parser_output(
                message_type="clarification",
                domain_hint=None,
                intent_hint=None,
                entities=[],
                correction=False,
            )
        )
        stub_access()
        result, trace = _run(session_factory, _envelope())
        assert result.branch_kind == "clarify_menu"
        _assert_trace_is_legible(trace)

    def test_not_supported(self, session_factory, seeded, stub_parser, stub_access):
        stub_parser(_parser_output(domain_hint="goods_receive", intent_hint="check_goods_receive"))
        stub_access()
        result, trace = _run(session_factory, _envelope())
        assert result.branch_kind == "not_supported"
        _assert_trace_is_legible(trace)

    def test_business_query(self, session_factory, seeded, stub_parser, stub_access):
        stub_parser(_parser_output())
        stub_access()
        result, trace = _run(session_factory, _envelope())
        assert result.branch_kind == "business_query"
        _assert_trace_is_legible(trace)

    def test_media_denied(self, session_factory, seeded, stub_parser, stub_access):
        """PLAN-chatbot-media-into-turn.md, S2: a voice note with no url never reaches
        the parser (`stub_parser` proves nothing calls it) or APPLY, and closes on the
        media intake's own denial alone. The trace still reads received -> media_intake
        (carrying its own `decision`, and a `notice` when the intake produced one) ->
        replied -> remembered - the SAME four-stage shape every other declared branch
        kind gets, even though this one short-circuits before the parser is even set up.
        """
        stub_parser()
        stub_access()
        envelope = _envelope()
        envelope.message["message"]["message"] = {"type": "audio", "attachment": {"type": "audio"}}
        result, trace = _run(session_factory, envelope)
        assert result.branch_kind == "media_denied"
        stages = [entry["stage"] for entry in trace if entry.get("kind") is None]
        assert stages == ["received", "media_intake", "replied", "remembered"]
        media_record = next(entry for entry in trace if entry.get("stage") == "media_intake")
        assert media_record["facts"]["decision"] == "no_url"
        _assert_trace_is_legible(trace)

    def test_ai_not_allowed(self, session_factory, seeded, stub_parser, stub_access):
        """AI-ALLOWED-GATE: a contact the AI may not answer closes silently at `received`.
        The one trace record reads as a sentence and says no reply was sent."""
        session_factory().execute(
            text("UPDATE respond_contacts SET chatbot_allowed = false WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        )
        session_factory().commit()
        stub_parser()
        stub_access()
        result, trace = _run(session_factory, _envelope())
        assert result.branch_kind == "ai_not_allowed"
        records = [entry for entry in trace if entry.get("kind") is None]
        assert [entry["stage"] for entry in records] == ["received"]
        assert records[0]["status"] == "skipped"
        assert records[0]["summary"] == "AI not allowed for this contact; sent no reply"
        _assert_trace_is_legible(trace)

    def test_every_declared_branch_kind_has_a_test_above(self) -> None:
        """A new BRANCH_KINDS entry must add a scenario here, not silently go untested.

        `escalate_offer`, `out_of_scope`, `offer_hold`, `stock_denied`, `demand_qty` are
        NOT in `tested` below - their scenarios here were retired 16 Sep 2026 (AC-1592,
        see the module docstring); trace-legibility coverage for `escalate_offer`/
        `out_of_scope` lives in `test_rearch_s3_team_pick_and_866.py` instead, so this
        completeness check is scoped to the branch kinds this file itself still drives.
        """
        tested = {
            "access_denied",
            "ideate",
            "escalation_declined",
            "check_promotion",
            "low_signal",
            "clarify_menu",
            "not_supported",
            "business_query",
            "media_denied",
            "ai_not_allowed",
        }
        retired_elsewhere_or_flagged = {
            "escalate_offer",
            "out_of_scope",
            "offer_hold",
            "stock_denied",
            "demand_qty",
        }
        assert tested | retired_elsewhere_or_flagged == set(BRANCH_KINDS)
