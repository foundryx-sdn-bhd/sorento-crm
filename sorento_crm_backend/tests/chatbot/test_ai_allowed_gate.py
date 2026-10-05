"""AI-ALLOWED-GATE: the engine ends a turn silently for a contact the AI may not answer.

Plan `documentation/plans/chatbot/PLAN-ai-allowed-gate-5oct.md`, UAC AC-AI-3 .. AC-AI-8.

`respond_contacts.chatbot_allowed` (BOOLEAN NOT NULL, default true). The gate sits in
`engine._run_turn` right after the contact's respond id is known, BEFORE the worker offload
and before any session-vars read. A contact is allowed when ANY `respond_contacts` row with
that `respond_io_id` has the flag true; no row at all, or no true row, is "not allowed".

A gated turn: no parser call, no reply, no actions (not even the `is_human_intervened`
`update_contact_fields`), no session write, no ticket, no enqueue. It is still a recorded,
terminal turn: `done` at `received`, branch `ai_not_allowed`, one trace line.

Reuses `test_engine.py`'s harness: the real engine over a blank Postgres schema.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.config import settings
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod

from tests.chatbot.test_engine import CONTACT_ID, _envelope, seeded, stub_access, stub_parser  # noqa: F401

pytestmark = pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")

GATE_SUMMARY = "AI not allowed for this contact; sent no reply"


def _set_flag(session_factory, allowed: bool) -> None:
    """The contact page's "AI replies allowed" switch, set on the seeded row."""
    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET chatbot_allowed = :a WHERE respond_io_id = :c"),
        {"a": allowed, "c": str(CONTACT_ID)},
    )
    db.commit()


def _add_second_row(session_factory, *, allowed: bool) -> None:
    """A second respond_contacts row with the SAME respond_io_id and a different phone."""
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars, chatbot_allowed) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb), :a)"
        ),
        {
            "cid": str(CONTACT_ID),
            "phone": f"+6013{uuid.uuid4().hex[:8]}",
            "sv": json.dumps({"variables": {}}),
            "a": allowed,
        },
    )
    db.commit()


def _turns(session_factory) -> list[ChatbotTurn]:
    return (
        session_factory()
        .query(ChatbotTurn)
        .filter(ChatbotTurn.contact_respond_id == str(CONTACT_ID))
        .all()
    )


def _session_vars(session_factory) -> Any:
    return session_factory().execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c ORDER BY phone_number"),
        {"c": str(CONTACT_ID)},
    ).all()


def _reply_text(result: Any) -> str:
    reply = result.reply if isinstance(result.reply, dict) else {}
    return str(reply.get("text") or "")


def _assert_silent(result: Any) -> None:
    assert result.branch_kind == "ai_not_allowed"
    assert result.status == "done"
    assert result.stage == "received"
    assert result.delegate is None
    assert result.actions == []
    assert "send_message" not in [a.get("kind") for a in (result.actions or [])]
    assert _reply_text(result) == ""


def _assert_closed_row(session_factory, result: Any) -> ChatbotTurn:
    row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
    assert row is not None
    assert row.status == "done"
    assert row.stage == "received"
    assert row.branch_kind == "ai_not_allowed"
    assert row.finished_at is not None
    assert row.error is None
    assert GATE_SUMMARY in [r.get("summary") for r in (row.trace or []) if isinstance(r, dict)]
    return row


class TestUnknownContact:
    """AC-AI-3: no respond_contacts row for the respond.io id. The blank schema has none,
    because this class never uses the `seeded` fixture."""

    def test_a_contact_with_no_row_gets_no_reply_and_no_actions(
        self, session_factory, stub_parser, stub_access
    ):
        calls: list[str] = []
        stub_parser(on_call=calls.append)
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        _assert_silent(result)
        assert calls == []

    def test_the_turn_is_recorded_and_closed_with_the_trace_line(
        self, session_factory, stub_parser, stub_access
    ):
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        _assert_closed_row(session_factory, result)
        assert len(_turns(session_factory)) == 1

    def test_it_never_reaches_the_generic_error_reply(self, session_factory, stub_parser, stub_access):
        """AC-AI-8: the session-vars 404 for an unknown contact must not turn into an error reply."""
        stub_parser()
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert result.status != "failed"
        assert result.branch_kind != "error"
        assert _reply_text(result) == ""


class TestBlockedContact:
    """AC-AI-4: the flag is false."""

    def test_a_blocked_contact_gets_no_reply_no_actions_and_no_parser_call(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        _set_flag(session_factory, False)
        calls: list[str] = []
        stub_parser(on_call=calls.append)
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        _assert_silent(result)
        assert calls == []

    def test_the_blocked_turn_row_is_closed_done_at_received_with_the_trace_line(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        _set_flag(session_factory, False)
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        _assert_closed_row(session_factory, result)

    def test_no_update_contact_fields_action_even_when_human_intervened_is_true(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        _set_flag(session_factory, False)
        stub_parser()
        stub_access()
        envelope = _envelope()
        envelope.contact["custom_fields"] = [{"name": "is_human_intervened", "value": "true"}]

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert result.branch_kind == "ai_not_allowed"
        assert result.actions == []

    def test_nothing_is_written_to_the_contact(self, session_factory, seeded, stub_parser, stub_access):
        _set_flag(session_factory, False)
        before = _session_vars(session_factory)
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "ai_not_allowed"
        assert _session_vars(session_factory) == before
        assert result.session_patch in (None, {})


class TestAllowedContact:
    """AC-AI-5: the flag is true and the turn runs exactly as today."""

    def test_an_allowed_contact_runs_the_business_lane(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        _set_flag(session_factory, True)
        calls: list[str] = []
        stub_parser(on_call=calls.append)
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "business_query"
        assert len(calls) == 1
        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        assert row.status == "done"
        assert row.stage == "sent"

    def test_a_fresh_row_defaults_to_allowed(self, session_factory, seeded):
        flag = session_factory().execute(
            text("SELECT chatbot_allowed FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        assert flag is True

    def test_human_intervened_handling_is_unchanged_for_an_allowed_contact(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """AC-AI-7."""
        _set_flag(session_factory, True)
        stub_parser()
        stub_access()
        envelope = _envelope()
        envelope.contact["custom_fields"] = [{"name": "is_human_intervened", "value": "true"}]

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert result.branch_kind == "business_query"
        assert result.actions[0]["kind"] == "update_contact_fields"


class TestGateOrdering:
    """AC-AI-8: the gate runs before the worker offload and before any ticket."""

    def test_a_gated_turn_never_enqueues_even_with_the_worker_offload_on(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ):
        _set_flag(session_factory, False)
        monkeypatch.setattr(settings, "chatbot_turn_on_worker", True, raising=False)
        monkeypatch.setattr(
            engine_mod, "enqueue_job", lambda *a, **k: pytest.fail("a gated turn was enqueued")
        )
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        _assert_silent(result)
        _assert_closed_row(session_factory, result)

    def test_an_unknown_contact_never_enqueues_either(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ):
        monkeypatch.setattr(settings, "chatbot_turn_on_worker", True, raising=False)
        monkeypatch.setattr(
            engine_mod, "enqueue_job", lambda *a, **k: pytest.fail("a gated turn was enqueued")
        )
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        _assert_silent(result)

    def test_a_gated_turn_never_takes_an_ordering_ticket(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ):
        _set_flag(session_factory, False)
        monkeypatch.setattr(engine_mod, "_s7_mode", lambda *a, **k: True)
        monkeypatch.setattr(
            engine_mod, "_ordering_redis", lambda: pytest.fail("a gated turn took a ticket")
        )
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        _assert_silent(result)


class TestDryRunAndDuplicate:
    def test_a_dry_run_envelope_is_gated_the_same_way(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        _set_flag(session_factory, False)
        calls: list[str] = []
        stub_parser(on_call=calls.append)
        stub_access()

        result = engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)

        _assert_silent(result)
        assert result.is_test is True
        assert calls == []

    def test_a_duplicate_delivery_replays_the_silent_turn(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        _set_flag(session_factory, False)
        stub_parser()
        stub_access()

        first = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        second = engine_mod.run_turn(_envelope(ingress="poller"), session_factory=session_factory)

        assert first.branch_kind == "ai_not_allowed"
        assert second.duplicate is True
        assert second.turn_id == first.turn_id
        assert second.actions == []
        assert len(_turns(session_factory)) == 1


class TestTwoRowsOneAllowed:
    """Two respond_contacts rows share a respond_io_id: allowed when ANY row is true."""

    def test_one_true_row_among_blocked_rows_allows_the_turn(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        _set_flag(session_factory, False)
        _add_second_row(session_factory, allowed=True)
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "business_query"

    def test_every_row_blocked_gates_the_turn(self, session_factory, seeded, stub_parser, stub_access):
        _set_flag(session_factory, False)
        _add_second_row(session_factory, allowed=False)
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        _assert_silent(result)
