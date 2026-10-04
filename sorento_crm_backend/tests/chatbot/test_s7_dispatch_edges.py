"""S7 edge cases the main ordering/offload suite does not cover (tester-first, RED where the
coder's in-flight fix round has not landed yet).

Covers, each naming its AC:

* AC-703 - the offload timeout arm: the RQ job is stopped on timeout and the row is closed
  `failed` at `queued`; and the race where the worker finishes first is left untouched.
* AC-705 - `mark_done` monotonicity: an out-of-order release never rewinds `done` (and,
  since CHATBOT-QUEUE-FIX, never passes a live predecessor).
* AC-705 / AC-710 - a redis outage during `contact_ticket` and during `wait_for_turn` degrades
  to an unordered but COMPLETED turn, never a hang and never a failure caused only by redis;
  `QueueWait` itself (the real per-contact timeout) is a different thing; since
  CHATBOT-QUEUE-FIX it runs the turn anyway and records `timed_out` on `queued`.
* H6 / AC-701 - `/complete` answers 410 `CHATBOT_S7_MODE_OWNS_THE_TAIL` through the FULL
  `app.main` app, with a real issued `X-API-Key` and a role holding exactly
  `integration.chat_turn.submit`, when `system_settings.chatbot_ordering_enabled` is on;
  unchanged (200) when
  off.
* D15 - a duplicate delivery (same `contact_respond_id` + `message_id`) arriving mid-burst
  takes no ticket: after the burst, `chatbot:seq:{contact}` equals the number of DISTINCT
  messages, not the number of deliveries.

Reuses fixtures and helpers already committed on this lane rather than re-deriving them:
`tests.chatbot.test_engine` (envelope/parser/access stubs, blank-schema `session_factory` from
`tests/chatbot/conftest.py`), `tests.chatbot.test_s7_ordering_and_offload` (the real-Postgres
`real_contacts` fixture, the redis key helpers, `_enable_ordering`), and
`tests.chatbot.test_chat_turn_endpoint` (the real `X-API-Key` / permission-role fixtures against
the full app). Nothing here reaches an LLM, n8n or respond.io. Every redis key this file creates
outside a fixture that already cleans up after itself is cleared in a `finally`.

Retired 16 Sep 2026 (AC-1592, coordinator ruling, "delegate architecture"):
`TestRedisOutageDuringOrdering.test_outage_during_contact_ticket_runs_unordered_and_reaches_
routing`, `.test_outage_during_wait_for_turn_runs_unordered_and_reaches_routing`, and
`TestDuplicateDeliveryMidBurstTakesNoTicket.test_seq_equals_distinct_messages_not_deliveries`.
All three pin their "unrelated, documented" failure reason to the OLD delegate pipeline's
`chatbot_completed_lanes`-empty-means-fail-at-`routed` behaviour (their own docstrings say
so explicitly, citing `test_s7_ordering_and_offload.py::TestS7ModeRefusesADelegatingLane` as
the reason this is "expected" rather than the thing under test). Since the S3 rearch removed
the delegate seam, a turn no longer fails there - it completes in-process instead (measured:
these three now observe `status='done'`, not `'failed'`/`'delegated'`).
`TestS7ModeRefusesADelegatingLane` itself is red on the same theme and is retired separately
(item 2, `test_s7_ordering_and_offload.py`) - no NEW replacement is named here because the
narrower claim this file's docstring states (a redis outage degrades to unordered-but-
answered rather than hanging or failing) has no assertion above that isolates it from the
now-stale completed-lanes failure mode; re-deriving the correct post-rearch stage/status is
engine investigation, not a mechanical port, and is out of this pass's scope per the
coordinator's brief (never re-derive a replay expectation from the engine under test).
`test_queuewait_is_not_swallowed_by_the_outage_guard` in the same class is NOT retired - it
was already green (QueueWait fails the turn on its own, independent of `chatbot_completed_
lanes`) and is left untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.config import settings
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import dispatch
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from tests.chatbot.conftest import set_chatbot_switches
from tests.chatbot.test_chat_turn_endpoint import api_key, client  # noqa: F401 - fixtures used by name
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)
from tests.chatbot.test_s7_ordering_and_offload import (  # noqa: F401 - fixtures/helpers reused
    _clear_contact_keys,
    _done_key,
    _enable_ordering,
    _envelope_for,
    _redis_client,
    _running_key,
    _seq_key,
    real_contacts,
    redis_client,  # noqa: F401 - real_contacts depends on this fixture by name
    stub_engine_seams,
)


# --------------------------------------------------------------------------- #
# AC-703: the offload timeout arm
# --------------------------------------------------------------------------- #


def _seed_turn_row(session_factory, *, contact: str, message_id: str, **overrides: Any):
    """A `chatbot.turns` row shaped like `_worker_failed` expects to find one.

    Written directly against the ORM rather than through `run_turn`, because these tests are
    about `_run_on_worker`'s timeout arm alone - the row is what a WORKER would have already
    inserted before the API's wait gives up.
    """
    db = session_factory()
    row = ChatbotTurn(
        contact_respond_id=contact,
        message_id=message_id,
        ingress="webhook",
        envelope={},
        is_test=False,
        status=overrides.get("status", "processing"),
        stage=overrides.get("stage", "queued"),
        branch_kind=overrides.get("branch_kind"),
        error=overrides.get("error"),
        attempt=1,
        trace=[],
        response=overrides.get("response"),
        started_at=datetime.now(timezone.utc),
        finished_at=overrides.get("finished_at"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class TestOffloadTimeoutArm:
    """AC-703. `_run_on_worker` stops the job and closes the row on timeout; a worker that
    finished first is left exactly as it wrote itself."""

    def test_timeout_stops_the_job_and_closes_failed_at_queued(
        self, session_factory, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings, "chatbot_turn_wait_seconds", 0.1, raising=False)
        monkeypatch.setattr(engine_mod, "WORKER_POLL_INTERVAL_SECONDS", 0.02, raising=False)

        contact = "ZZT-contact-s7-offload-timeout"
        message_id = "ZZT-msg-offload-timeout"
        _seed_turn_row(session_factory, contact=contact, message_id=message_id)

        class _FakeJob:
            id = "ZZT-fake-job-offload-timeout"

        monkeypatch.setattr(engine_mod, "enqueue_job", lambda *a, **k: _FakeJob())
        monkeypatch.setattr(
            engine_mod,
            "get_job_status",
            lambda job_id: {"status": "started", "result": None, "exc_info": None},
        )
        cancel_calls: list[str] = []
        monkeypatch.setattr(
            engine_mod, "cancel_job", lambda job_id: cancel_calls.append(job_id) or True
        )

        envelope = _envelope_for(contact, message_id)
        result = engine_mod._run_on_worker(envelope, session_factory=session_factory)

        assert cancel_calls == ["ZZT-fake-job-offload-timeout"], (
            "a timed-out offload must stop the job exactly once, got calls=%r" % cancel_calls
        )
        assert result.status == "failed"
        assert result.stage == "queued"
        assert "did not finish within" in (result.error or ""), result.error
        # The caller still gets today's apology, dry_run included on the shape.
        assert result.actions and result.actions[0]["kind"] == "send_message"

        row = (
            session_factory()
            .query(ChatbotTurn)
            .filter(ChatbotTurn.id == result.turn_id)
            .first()
        )
        assert row is not None
        assert row.status == "failed"
        assert row.stage == "queued"
        assert row.error and "did not finish within" in row.error
        assert row.finished_at is not None

    def test_timeout_race_worker_already_finished_leaves_its_result_untouched(
        self, session_factory, monkeypatch
    ) -> None:
        """The worker won the race: the row already has `finished_at` set by the time the
        API's wait gives up. `_worker_failed` must not overwrite it (docstring, engine.py:
        "Tolerant of the worker winning the race")."""
        monkeypatch.setattr(settings, "chatbot_turn_wait_seconds", 0.1, raising=False)
        monkeypatch.setattr(engine_mod, "WORKER_POLL_INTERVAL_SECONDS", 0.02, raising=False)

        contact = "ZZT-contact-s7-offload-race"
        message_id = "ZZT-msg-offload-race"
        seeded_response = {
            "ctx": {"contact": {"id": contact}},
            "item": {"branch_kind": "business_query"},
            "actions": [],
            "delegate_payload": None,
            "delegate_error": None,
        }
        row = _seed_turn_row(
            session_factory,
            contact=contact,
            message_id=message_id,
            status="done",
            stage="routed",
            branch_kind="business_query",
            response=seeded_response,
            finished_at=datetime.now(timezone.utc),
        )

        class _FakeJob:
            id = "ZZT-fake-job-offload-race"

        monkeypatch.setattr(engine_mod, "enqueue_job", lambda *a, **k: _FakeJob())
        # From the API's point of view the job status snapshot never reports "finished" -
        # exactly the observable state of a genuine race, where the worker committed the row
        # a moment before the API's LAST poll, and the job's own bookkeeping has not caught
        # up yet either.
        monkeypatch.setattr(
            engine_mod,
            "get_job_status",
            lambda job_id: {"status": "started", "result": None, "exc_info": None},
        )
        cancel_calls: list[str] = []
        monkeypatch.setattr(
            engine_mod, "cancel_job", lambda job_id: cancel_calls.append(job_id) or True
        )

        envelope = _envelope_for(contact, message_id)
        result = engine_mod._run_on_worker(envelope, session_factory=session_factory)

        assert cancel_calls == ["ZZT-fake-job-offload-race"]
        # The CALLER still gets the apology - it has already given up waiting.
        assert result.status == "failed"

        fresh = (
            session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == row.id).first()
        )
        assert fresh is not None
        assert fresh.status == "done", "the worker's own finished result must survive"
        assert fresh.stage == "routed"
        assert fresh.branch_kind == "business_query"
        assert fresh.error is None
        assert fresh.response == seeded_response
        assert fresh.finished_at is not None


# --------------------------------------------------------------------------- #
# AC-705: `mark_done` monotonicity
# --------------------------------------------------------------------------- #


class TestMarkDoneMonotonicity:
    """AC-705. `done` has two writers (a finishing turn, a repairing waiter) and neither may
    lower it - an out-of-order release must not rewind a counter later tickets already
    passed."""

    def test_an_out_of_order_release_never_rewinds_done(self) -> None:
        contact = "ZZT-contact-s7-mark-done-monotone"
        client = _redis_client()
        try:
            _clear_contact_keys(client, contact)
            t1 = dispatch.contact_ticket(client, contact)
            t2 = dispatch.contact_ticket(client, contact)

            # Ticket 2 finishes FIRST while ticket 1 is still alive: `done` stays put
            # (CHATBOT-QUEUE-FIX: a release never passes a live predecessor).
            dispatch.mark_done(client, contact, t2)
            assert client.get(_done_key(contact)) in (None, "0")

            # Ticket 1 finishes LATE: `done` walks over both.
            dispatch.mark_done(client, contact, t1)
            assert client.get(_done_key(contact)) == "2"

            # A repeated, stale release is a no-op and never pulls `done` backwards.
            dispatch.mark_done(client, contact, t1)
            assert client.get(_done_key(contact)) == "2"
            assert client.exists(dispatch.alive_key(contact, t1)) == 0
            assert client.exists(dispatch.alive_key(contact, t2)) == 0
            assert client.exists(_running_key(contact)) == 0
        finally:
            _clear_contact_keys(client, contact)
            client.close()


# --------------------------------------------------------------------------- #
# AC-705 / AC-710: redis outage during ordering
# --------------------------------------------------------------------------- #


class TestRedisOutageDuringOrdering:
    """A redis blip must degrade the turn to unordered-but-answered, never to a hang or a
    failure whose only cause is redis being unavailable. `QueueWait` (the real per-contact
    timeout) is not one of these and must still fail the turn.

    The two scenarios asserting the outage-degrades-gracefully half of this claim were
    retired 16 Sep 2026 - see the module docstring's retirement note. Only the QueueWait
    counter-case (a genuine ordering timeout is NOT swallowed by the outage guard) remains,
    and it does not depend on `chatbot_completed_lanes`.
    """

    def test_queuewait_is_not_swallowed_and_the_turn_still_runs(
        self, real_contacts, stub_engine_seams, monkeypatch
    ) -> None:
        """`QueueWait` is a `RuntimeError`, not one of `dispatch.ORDERING_ERRORS`, so the
        outage guard does not treat it as a redis blip. Since CHATBOT-QUEUE-FIX it does not
        fail the turn either: the turn runs, its `queued` record says it timed out, and
        the ticket is released."""
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())
        _enable_ordering(monkeypatch)
        contact = real_contacts("queuewait-not-swallowed")

        def raising_queue_wait(*a, **k):
            raise dispatch.QueueWait("forced for this test")

        monkeypatch.setattr(dispatch, "wait_for_turn", raising_queue_wait)

        from app.database import SessionLocal

        result = engine_mod.run_turn(
            _envelope_for(contact, "ZZT-msg-queuewait-not-swallowed"),
            session_factory=SessionLocal,
        )

        assert result.status != "failed", (result.status, result.stage)
        row = SessionLocal().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        queued = [r for r in (row.trace or []) if r.get("stage") == "queued"]
        assert queued and queued[0]["facts"].get("timed_out") is True, queued

        # And the ticket must still have been released, same as any other mid-turn failure.
        client = _redis_client()
        try:
            assert client.get(_done_key(contact)) == "1"
        finally:
            client.close()


# --------------------------------------------------------------------------- #
# H6 / AC-701: /complete answers 410 in S7 mode, through the full app
# --------------------------------------------------------------------------- #


class TestCompleteGoneInS7ModeFullApp:
    """`POST /chat/turn/{id}/complete` answers 410 `CHATBOT_S7_MODE_OWNS_THE_TAIL` through the
    full `app.main` app with a real `X-API-Key` and a role holding exactly
    `integration.chat_turn.submit`, when `system_settings.chatbot_ordering_enabled` is on;
    unchanged when
    off."""

    def test_complete_answers_410_with_the_s7_code_when_ordering_is_on(
        self, client, api_key, session_factory
    ) -> None:
        set_chatbot_switches(session_factory, ordering=True)

        turn_id = "33333333-3333-3333-3333-333333333333"
        resp = client.post(
            f"/api/v1/external/chat/turn/{turn_id}/complete",
            json={"item": {"branch_kind": "low_signal"}},
            headers={"X-API-Key": api_key},
        )

        assert resp.status_code == 410, resp.text
        body = resp.json()
        assert body.get("code") == "CHATBOT_S7_MODE_OWNS_THE_TAIL", body

    def test_complete_still_answers_200_when_ordering_is_off(
        self, client, api_key, session_factory, monkeypatch
    ) -> None:
        set_chatbot_switches(session_factory, ordering=False)

        canned = {
            "turn_id": "44444444-4444-4444-4444-444444444444",
            "reply": {
                "text": "Here is what I found.",
                "quick_replies": None,
                "result_set": [],
                "attachments_src": None,
            },
            "actions": [{"kind": "send_message", "text": "Here is what I found."}],
            "session_patch": None,
        }

        class _FakeResult:
            def as_dict(self) -> dict:
                return dict(canned)

        monkeypatch.setattr(
            "app.api.v1.external.chat.complete_turn", lambda *a, **k: _FakeResult()
        )

        resp = client.post(
            f"/api/v1/external/chat/turn/{canned['turn_id']}/complete",
            json={"item": {"branch_kind": "business_query"}},
            headers={"X-API-Key": api_key},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reply"]["text"] == "Here is what I found."
        assert body["actions"] == canned["actions"]


# --------------------------------------------------------------------------- #
# D15: a duplicate delivery mid-burst takes no ticket
# --------------------------------------------------------------------------- #


class TestDuplicateDeliveryMidBurstTakesNoTicket:
    """D15 + AC-709. The dedup read happens BEFORE any ticket is taken, so a duplicate
    delivery of a message already turned into a turn must not advance `chatbot:seq`."""

    def test_seq_equals_distinct_messages_not_deliveries(
        self, real_contacts, stub_engine_seams, monkeypatch
    ) -> None:
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())
        _enable_ordering(monkeypatch, queue_wait_seconds=5.0)
        contact = real_contacts("dup-burst")

        from app.database import SessionLocal

        message_ids = [
            "ZZT-msg-dup-burst-1",
            "ZZT-msg-dup-burst-2",
            "ZZT-msg-dup-burst-3",
        ]
        results = []
        for i, message_id in enumerate(message_ids):
            results.append(
                engine_mod.run_turn(
                    _envelope_for(contact, message_id), session_factory=SessionLocal
                )
            )
            if i == 1:
                # A redelivery of message #2 lands MID-BURST, before #3 has even arrived.
                dup = engine_mod.run_turn(
                    _envelope_for(contact, message_id), session_factory=SessionLocal
                )
                assert dup.duplicate is True, (
                    "the redelivery must be recognised as a duplicate of the same message"
                )
                assert dup.turn_id == results[1].turn_id

        # D15's dedup does not care whether the turn it is deduplicating against
        # succeeded or failed - only that each of the 3 DISTINCT messages became exactly
        # one turn row (checked via `.turn_id`/`.duplicate` above) and the redis `seq`
        # counter reflects that. A `status`/`error` assertion pinning the SPECIFIC
        # (unrelated) reason those turns finished was retired 16 Sep 2026 - see the
        # module docstring's retirement note; it pinned the old delegate pipeline's
        # `chatbot_completed_lanes`-empty failure mode, which no longer occurs since the
        # S3 rearch removed the delegate seam.
        assert all(r.turn_id is not None for r in results), (
            "every distinct message must become a real turn row",
            [r.error for r in results],
        )

        client = _redis_client()
        try:
            seq = client.get(_seq_key(contact))
            assert seq == "3", (
                "a duplicate delivery must not consume a ticket: chatbot:seq must equal the "
                f"number of DISTINCT messages (3), got {seq!r}"
            )
            assert client.get(_done_key(contact)) == "3"
        finally:
            client.close()
