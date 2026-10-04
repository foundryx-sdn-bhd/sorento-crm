"""AC-MEM014 (round 3 UAC, merged 5b110df8; PLAN section 6.0) - the per-contact ordering
ticket the trace drawer's Order panel reads back.

**Round 3 change, listed**:

* the successful-wait `order` projection (`trace_detail._order`, fed by `engine.py`'s
  `turn_trace.add("queue", {...})` on a successful wait) must carry `wait_ms` - today it
  writes `waited_ms`. Same rename shape as `past` -> `episodes` elsewhere in this round.
* a QUEUE TIMEOUT (`dispatch.QueueWait`) must record the ticket it was waiting for on the
  `queued` stage's `facts` (since CHATBOT-QUEUE-FIX the turn then RUNS; before, the
  generic `except Exception` handler in `engine.run_turn` records only
  `facts={"stage": stage[0]}`, with no ticket at all (verified by reading
  `app/services/chatbot/engine.py` lines ~980-991: the `ticket` local never reaches the
  failure record).
* a dry run never takes a ticket at all (`ordered = _s7_mode(db, switches) and not
  dry_run`) and so records neither `ticket` nor `wait_ms` - already true architecturally,
  kept here as a regression guard rather than a fresh gap.

Stubs the Redis ticket seam directly (`dispatch.contact_ticket`, `dispatch.wait_for_turn`,
`dispatch.start_heartbeat`, `dispatch.mark_done`) and flips `engine_mod._s7_mode` at the
predicate, the same shape `test_s7_ordering_and_offload.py::_enable_ordering` uses - the
ORDERING CONTRACT is under test here, not concurrency, so a real Redis buys nothing.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import dispatch, engine as engine_mod, trace_detail
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access, stub_parser  # noqa: F401


def _seed_contact(session_factory, contact_respond_id: str) -> None:
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {
            "cid": contact_respond_id,
            "phone": f"+6012{uuid.uuid4().hex[:8]}",
            "sv": json.dumps({"variables": {}}),
        },
    )
    db.commit()


class _NoBeat:
    def stop(self) -> None:
        pass


def _enable_ordering(monkeypatch, *, ticket: int = 7) -> None:
    monkeypatch.setattr(engine_mod, "_s7_mode", lambda *a, **k: True)
    monkeypatch.setattr(engine_mod, "_ordering_redis", lambda: object())
    monkeypatch.setattr(dispatch, "contact_ticket", lambda redis, contact: ticket)
    monkeypatch.setattr(dispatch, "start_heartbeat", lambda redis, contact, tk: _NoBeat())
    monkeypatch.setattr(dispatch, "mark_done", lambda redis, contact, tk: None)


def _turn_row(session_factory, turn_id: str) -> ChatbotTurn:
    return session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()


class TestSuccessfulWaitRecordsWaitMs:
    def test_a_ticket_that_waited_records_ticket_and_wait_ms(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid)
        stub_access()
        stub_parser()
        _enable_ordering(monkeypatch, ticket=7)
        monkeypatch.setattr(
            dispatch, "wait_for_turn", lambda redis, contact, ticket, *, timeout_s: None
        )

        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-ticket-ok-1"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        row = _turn_row(session_factory, result.turn_id)
        order = trace_detail.compose_trace_detail(row)["order"]
        assert order is not None, "a turn that took a ticket must carry an `order` projection"
        assert order.get("ticket") == 7, order
        assert order.get("wait_ms") is not None, (
            f"round 3 (AC-MEM014) renames `waited_ms` to `wait_ms` - got keys {sorted(order)}"
        )


class TestQueueTimeoutRecordsTheTicket:
    def test_a_queue_timeout_runs_the_turn_and_names_the_ticket_it_waited_for(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid)
        stub_access()
        stub_parser()
        _enable_ordering(monkeypatch, ticket=9)

        def _timeout(redis, contact, ticket, *, timeout_s):
            raise dispatch.QueueWait(f"gave up waiting for ticket {ticket}")

        monkeypatch.setattr(dispatch, "wait_for_turn", _timeout)

        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-ticket-timeout-1"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        # CHATBOT-QUEUE-FIX (prod 1 Oct): a timeout no longer fails the turn with the
        # generic error; the turn runs, and the queued record says it timed out.
        assert result.status != "failed", result
        assert result.reply["text"] != engine_mod.GENERIC_ERROR_REPLY
        row = _turn_row(session_factory, result.turn_id)
        detail = trace_detail.compose_trace_detail(row)
        queued_stage = next((s for s in detail["stages"] if s.get("name") == "queued"), None)
        assert queued_stage is not None, detail["stages"]
        assert queued_stage["facts"].get("timed_out") is True, queued_stage["facts"]
        assert queued_stage["facts"].get("ticket") == 9, (
            f"a queue timeout must record the ticket it was waiting for, got facts="
            f"{queued_stage['facts']}"
        )


class TestDryRunNeverTakesATicket:
    def test_a_dry_run_records_no_ticket_and_no_wait_ms(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid)
        stub_access()
        stub_parser()
        monkeypatch.setattr(engine_mod, "_s7_mode", lambda *a, **k: True)
        for name in ("contact_ticket", "start_heartbeat", "wait_for_turn", "mark_done"):
            monkeypatch.setattr(
                dispatch,
                name,
                lambda *a, **k: pytest.fail(
                    f"dispatch.{name} was called on a DRY RUN - a dry run never takes a ticket"
                ),
            )

        envelope = _envelope(test_run_id="ZZT-ticket-dry-1")
        assert envelope.dry_run is True
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        row = _turn_row(session_factory, result.turn_id)
        order = trace_detail.compose_trace_detail(row)["order"]
        assert order is None, f"a dry run must record neither ticket nor wait_ms, got {order}"
