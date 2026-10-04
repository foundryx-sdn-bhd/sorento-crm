"""S7 - thin spine + CRM per-contact ordering + optional worker offload (RED, tester-first).

Covers AC-701, AC-703, AC-704, AC-709, AC-710, AC-713, H12; a best-effort static check for
H6. AC-702, AC-705 and AC-712 already have tests elsewhere on this lane
(`test_chat_turn_endpoint.py::TestDryRunEndpointZeroWrites`, `test_d15_duplicate_race.py`) and
are deliberately NOT duplicated here. AC-706 to AC-708, AC-711, AC-714 are n8n/E2E and out of
scope for pytest.

**Written BEFORE `app/services/chatbot/dispatch.py` exists.** As of this commit the lane head
(4a5920f66) has no `dispatch` module - the plan's own S7 section names it
(`documentation/plans/chatbot/PLAN-chatbot-turn-engine.md` line 604: "`dispatch.py`: redis
ticket FIFO per contact inside the request") but nothing has built it yet. A `dispatch.py`
carrying `reinject_envelope` (AC-705) exists on the separate, unmerged
`feat/chatbot-turn-engine-s2b` worktree - that is a different function for a different AC and
is untouched here; do not assume it lands as part of this file going green.

**The contract this file locks in for the coder** (the plan's prose, made concrete).
SUPERSEDED IN PART by CHATBOT-QUEUE-FIX (1 Oct 2026, `tests/chatbot/test_queue_fix_1oct.py`):
the shared `running` key, `mark_running` and the stall-grace repair are gone, replaced by a
per-ticket heartbeat key (`dispatch.alive_key`, `dispatch.start_heartbeat`); a `QueueWait`
no longer fails the turn, it runs it. The bullets below are the original S7 contract:

* Redis keys, per contact: `chatbot:seq:{contact}` (INCR'd ticket counter, 1h TTL),
  `chatbot:done:{contact}` (last-completed ticket, absent == 0), `chatbot:running:{contact}`
  (present while a ticket is actively being worked; absent otherwise - deleted, not expired,
  so its ABSENCE is the death signal `wait_for_turn` watches for).
* `dispatch.contact_ticket(redis, contact) -> int` - `INCR chatbot:seq:{contact}`, then
  `EXPIRE` 3600s, returns the new ticket.
* `dispatch.mark_running(redis, contact, ticket) -> None` - sets `chatbot:running:{contact}`.
* `dispatch.mark_done(redis, contact, ticket) -> None` - sets `chatbot:done:{contact} = ticket`
  and clears `chatbot:running:{contact}`. Called in the engine's `finally`, so a mid-turn
  exception still releases the next waiter (AC-704).
* `dispatch.wait_for_turn(redis, contact, ticket, *, timeout_s) -> None` - polls every
  `dispatch.POLL_INTERVAL_SECONDS` (200 ms) until `chatbot:done:{contact} >= ticket - 1`.
  While waiting, if `chatbot:running:{contact}` has been ABSENT for more than
  `dispatch.STALL_GRACE_SECONDS` (2 s) - tracked by the poller itself, not stored in redis -
  it self-heals: sets `done = ticket - 1` and returns, rather than waiting out the predecessor
  that will never finish. Exceeding `timeout_s` with no repair raises `dispatch.QueueWait`.
  Both constants are module-level so a test can shrink them.
* `engine.run_turn`, at the top of the wrapped stage-runner (`stage[0]` already exists for
  exactly this reason - the generic `except Exception` in `run_turn` records whatever
  `stage[0]` says), sets `stage[0] = "queued"` and - only when
  `system_settings.chatbot_ordering_enabled` is `True` (default `False` until S7 promotes) - calls
  `dispatch.contact_ticket` then `dispatch.wait_for_turn(timeout_s=settings.
  chatbot_queue_wait_seconds)`. A `QueueWait` propagates out of the ordering call exactly like
  any other exception the existing handler already catches - `stage[0]` is `"queued"`, so the
  turn closes `failed`, `stage="queued"`, and the caller still gets `GENERIC_ERROR_REPLY` as a
  `send_message` action (AC-710's second half; the shape is `engine._failed_result`, already
  covered for other stages by `test_engine_failure_paths.py`). On success it calls
  `dispatch.mark_running`, sets `stage[0] = "received"`, and wraps the rest of the turn in a
  `finally: dispatch.mark_done(...)` so a later exception still releases the ticket (AC-704).
  With the flag off (default), `dispatch` is never imported or called (AC-701).
* Worker offload: `settings.chatbot_turn_on_worker` (default `False`). When `True`,
  `run_turn` enqueues via `app.services.queue_service.enqueue_job(..., queue_name="chat")`
  (imported into `engine.py` at module level, same shape as `app/api/v1/external/media.py`'s
  `enqueue_job` import) instead of running in-process, and waits up to `settings.
  chatbot_turn_wait_seconds` (default 60) for the result - this file asserts only the
  observable contract (`queue_name="chat"`, the job's result becomes the returned
  `TurnResult`), not a specific polling primitive, so the coder is free to reuse
  `app.services.queue_service.get_job_status` or poll `rq.job.Job` directly.
* `chat` joins `worker.QUEUES` (a `fast` role - it is request-latency-bound the same way
  `respond_io` and `media` are) and therefore `worker.DEFAULT_QUEUES`; extended (not
  duplicated) in `tests/test_worker_queue_defaults.py`.

Nothing here reaches an LLM, n8n or respond.io. Concurrency tests use real, committed
Postgres via `SessionLocal` and a real local Redis (`settings.redis_url` - confirmed reachable
in this environment via `redis-cli ping`), the same reasoning `test_d15_duplicate_race.py`
gives for why a single-connection rollback-scoped fixture cannot show two real threads racing.
Marker-prefixed (`ZZT-`) and cleaned up by hand in `finally`.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

import pydantic
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import Envelope, TurnRequest
from app.services.chatbot.head import parser as parser_mod
from tests.chatbot.conftest import set_chatbot_switches
from tests.chatbot.test_engine import (  # noqa: F401  - fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)

REDIS_URL = settings.redis_url


def _redis_client():
    import redis

    return redis.from_url(REDIS_URL, decode_responses=True)


def _seq_key(contact: str) -> str:
    return f"chatbot:seq:{contact}"


def _done_key(contact: str) -> str:
    return f"chatbot:done:{contact}"


def _running_key(contact: str) -> str:
    """Retired by CHATBOT-QUEUE-FIX; kept so the assertions that it is never left set hold."""
    return f"chatbot:running:{contact}"


def _clear_contact_keys(client, contact: str) -> None:
    client.delete(_seq_key(contact), _done_key(contact), _running_key(contact))
    for key in client.scan_iter(f"chatbot:alive:{contact}:*"):
        client.delete(key)


def _envelope_for(contact: str, message_id: str, *, ingress: str = "webhook") -> Envelope:
    return Envelope(
        contact={"id": contact, "firstName": "ZZT", "custom_fields": []},
        message={
            "event_type": "message.received",
            "contact": {"id": contact},
            "message": {
                "messageId": message_id,
                "contactId": contact,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": "price for SRTWC8517"},
            },
        },
        ingress=ingress,
    )


@pytest.fixture()
def redis_client():
    client = _redis_client()
    yield client
    client.close()


@pytest.fixture()
def real_contacts(redis_client):
    """Seed N real, committed `respond_contacts` rows; clean up DB + redis in `finally`."""
    created: list[str] = []

    def _seed(suffix: str) -> str:
        contact = f"ZZT-contact-s7-{suffix}"
        db = SessionLocal()
        try:
            db.execute(
                text(
                    "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
                    "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb)) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"cid": contact, "phone": f"+6000000{suffix}", "sv": json.dumps({"variables": {}})},
            )
            db.commit()
        finally:
            db.close()
        created.append(contact)
        return contact

    yield _seed

    cleanup = SessionLocal()
    try:
        for contact in created:
            cleanup.query(ChatbotTurn).filter(
                ChatbotTurn.contact_respond_id == contact
            ).delete(synchronize_session=False)
            cleanup.execute(
                text("DELETE FROM respond_contacts WHERE respond_io_id = :cid"), {"cid": contact}
            )
        cleanup.commit()
    finally:
        cleanup.close()
    for contact in created:
        _clear_contact_keys(redis_client, contact)


@pytest.fixture()
def stub_engine_seams(monkeypatch):
    """Same seam stack as `test_d15_duplicate_race.py`'s `stub_engine_seams`."""

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True,
            "decision": "allow",
            "agent_name": "General Enquiries",
            "attributes": None,
            "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")


def _complete_in_the_crm(monkeypatch) -> None:
    """Route these turns to a lane the CRM FINISHES, so S7 mode has a tail to run.

    S6c is what makes this possible: before it, `business_query` was not in
    `CRM_COMPLETED_BRANCH_KINDS` and an S7-mode turn on it could only be `failed`. It is
    now, but the business arms need `chatbot_business_lane_enabled`, a resolver, a tool
    search and two MCP probes - none of which these tests are about. `clarify_menu` is the
    cheapest CRM-completed lane: the copy registry and the tail, no network, no model call.

    `_enabled_lanes` is patched rather than the settings ROW because these tests run
    against the real database, and editing a singleton every other test reads is how a
    suite starts failing in whatever order it happens to run.
    """
    monkeypatch.setattr(
        engine_mod, "_enabled_lanes", lambda db, row=None: frozenset({"clarify_menu"})
    )
    monkeypatch.setattr(
        parser_mod,
        "parse",
        lambda config, user_block: _parser_output(
            message_type="clarification", domain_hint=None, user_goal="checking stock"
        ),
    )


def _enable_ordering(monkeypatch, *, queue_wait_seconds: float = 45.0) -> None:
    """Turn S7 mode on for the engine, at the predicate rather than at the settings ROW.

    AC-810 made S7 mode `system_settings.chatbot_ordering_enabled`, and every turn in this
    file runs on `SessionLocal` - the real database. The same reason `_nothing_enabled`
    patches `_enabled_lanes` instead of the singleton applies here and applies harder: a
    test that died between setting the column and restoring it would leave S7 mode on for
    the whole box, and n8n's `/complete` calls would start answering 410. The column that
    actually drives this predicate is graded in `test_s8_switches_in_settings.py`, on a
    blank schema, where flipping it costs nothing.
    """
    monkeypatch.setattr(engine_mod, "_s7_mode", lambda *args, **kwargs: True)
    monkeypatch.setattr(settings, "chatbot_queue_wait_seconds", queue_wait_seconds, raising=False)


class TestOrderingFlagDefaultOffBypassesTickets:
    """AC-701. Off by default, and off must mean OFF - no redis, no dispatch import cost."""

    def test_ordering_flag_default_off_bypasses_tickets(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        assert engine_mod._s7_mode(session_factory()) is False, (
            "AC-810: S7 mode defaults off, and a blank install with no system_settings row "
            "at all reads as off too - the safe direction"
        )

        from app.services.chatbot import dispatch  # noqa: F401 - may not exist yet (RED)

        for name in ("contact_ticket", "start_heartbeat", "wait_for_turn", "mark_done"):
            monkeypatch.setattr(dispatch, name, lambda *a, **k: pytest.fail(
                f"dispatch.{name} was called with the ordering flag OFF"
            ))

        stub_parser()
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        # Ported (AC-1592): `business_query` is in `CRM_COMPLETED_BRANCH_KINDS`
        # unconditionally today (S6c full coverage), so a bare default turn now
        # completes `done` in-process rather than delegating or failing - "ran to
        # completion either way" still holds, `"done"` is simply a third way to.
        assert result.status in ("delegated", "failed", "done")
        assert result.branch_kind == "business_query"


class TestPerContactOrdering:
    """AC-709 / H30 / H31. Same contact serialises; different contacts run in parallel."""

    def test_same_contact_runs_in_arrival_order(
        self, real_contacts, stub_engine_seams, monkeypatch
    ) -> None:
        _enable_ordering(monkeypatch)
        contact = real_contacts("order-a")

        lock = threading.Lock()
        timeline: list[tuple[str, float]] = []
        SLOW_SECONDS = 0.4

        def fake_parse(config, user_block):
            with lock:
                timeline.append(("start", time.monotonic()))
            time.sleep(SLOW_SECONDS)
            with lock:
                timeline.append(("end", time.monotonic()))
            return _parser_output()

        monkeypatch.setattr(parser_mod, "parse", fake_parse)

        results: list[Any] = [None, None]
        errors: list[BaseException | None] = [None, None]

        def _call(slot: int, message_id: str) -> None:
            try:
                results[slot] = engine_mod.run_turn(
                    _envelope_for(contact, message_id), session_factory=SessionLocal
                )
            except BaseException as exc:  # noqa: BLE001
                errors[slot] = exc

        t1 = threading.Thread(target=_call, args=(0, "ZZT-msg-order-a-1"))
        t2 = threading.Thread(target=_call, args=(1, "ZZT-msg-order-a-2"))
        t1.start()
        time.sleep(0.05)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert errors == [None, None], f"unexpected exception(s): {errors}"
        assert results[0] is not None and results[1] is not None

        starts = [ts for kind, ts in timeline if kind == "start"]
        ends = [ts for kind, ts in timeline if kind == "end"]
        assert len(starts) == 2 and len(ends) == 2, timeline
        # The whole point of AC-709: the second parser call must not START until the
        # first one has ENDED. Today, with no ordering wired in, both start ~50ms apart
        # and this fails because starts[1] < ends[0].
        assert starts[1] >= ends[0], (
            f"the second turn's parser call started before the first finished: {timeline}"
        )

        client = _redis_client()
        try:
            assert client.get(_seq_key(contact)) == "2"
        finally:
            client.close()

    def test_different_contacts_run_concurrently(
        self, real_contacts, stub_engine_seams, monkeypatch
    ) -> None:
        contact_a = real_contacts("par-a")
        contact_b = real_contacts("par-b")
        _enable_ordering(monkeypatch)

        lock = threading.Lock()
        intervals: dict[str, tuple[float, float]] = {}
        SLOW_SECONDS = 0.4

        # One shared `parser_mod.parse` (it is a module-level function, not per-call), so
        # each thread tells it which contact it is via a thread-local rather than a
        # parser argument - `parse`'s signature carries no contact. Both threads write
        # disjoint dict keys under the lock, so this is race-free for what it measures.
        current_contact = threading.local()

        def shared_fake_parse(config, user_block):
            start = time.monotonic()
            time.sleep(SLOW_SECONDS)
            end = time.monotonic()
            with lock:
                intervals[current_contact.value] = (start, end)
            return _parser_output()

        monkeypatch.setattr(parser_mod, "parse", shared_fake_parse)

        def _run(contact: str) -> None:
            current_contact.value = contact
            engine_mod.run_turn(
                _envelope_for(contact, f"ZZT-msg-{contact}"), session_factory=SessionLocal
            )

        t1 = threading.Thread(target=_run, args=(contact_a,))
        t2 = threading.Thread(target=_run, args=(contact_b,))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert set(intervals) == {contact_a, contact_b}
        (a_start, a_end) = intervals[contact_a]
        (b_start, b_end) = intervals[contact_b]
        overlap = min(a_end, b_end) - max(a_start, b_start)
        assert overlap > 0, (
            f"two different contacts must run concurrently, got no overlap: {intervals}"
        )


class TestStalledCounterRepair:
    """AC-710 / H30 / H31."""

    def test_stalled_counter_is_repaired(self, redis_client, monkeypatch) -> None:
        """A predecessor that died holding its ticket is walked past once its liveness
        lapses (CHATBOT-QUEUE-FIX: per-ticket heartbeat, not a shared `running` key)."""
        from app.services.chatbot import dispatch

        monkeypatch.setattr(dispatch, "ALIVE_TTL_SECONDS", 0.3, raising=False)
        monkeypatch.setattr(dispatch, "HEARTBEAT_INTERVAL_SECONDS", 0.05, raising=False)
        monkeypatch.setattr(dispatch, "POLL_INTERVAL_SECONDS", 0.02, raising=False)

        contact = "ZZT-contact-s7-stall"
        _clear_contact_keys(redis_client, contact)
        dispatch.contact_ticket(redis_client, contact)  # ticket 1: dies, never released
        ticket = dispatch.contact_ticket(redis_client, contact)
        beat = dispatch.start_heartbeat(redis_client, contact, ticket)
        try:
            started = time.monotonic()
            dispatch.wait_for_turn(redis_client, contact, ticket=ticket, timeout_s=5)
            elapsed = time.monotonic() - started
        finally:
            beat.stop()

        assert elapsed < 2.0, "must not wait anywhere near the full timeout to self-heal"
        assert redis_client.get(_done_key(contact)) == "1", (
            "the dead predecessor's ticket must be settled to done=ticket-1"
        )
        _clear_contact_keys(redis_client, contact)

    def test_queue_wait_timeout_runs_the_turn(
        self, real_contacts, stub_engine_seams, stub_parser, monkeypatch, redis_client
    ) -> None:
        """CHATBOT-QUEUE-FIX (prod 1 Oct, tickets 2 and 4 of contact 900000081): a
        predecessor still alive past the cap no longer fails this turn with the generic
        error. The turn runs, and the trace says it waited out the budget."""
        _enable_ordering(monkeypatch, queue_wait_seconds=0.3)
        stub_parser()
        from app.services.chatbot import dispatch

        monkeypatch.setattr(dispatch, "POLL_INTERVAL_SECONDS", 0.02, raising=False)

        contact = real_contacts("queue-timeout")
        redis_client.set(_seq_key(contact), 1)  # this run's ticket becomes 2
        redis_client.set(dispatch.alive_key(contact, 1), 1, ex=30)  # ticket 1 genuinely alive
        redis_client.delete(_done_key(contact))  # ticket 1 never finishes

        result = engine_mod.run_turn(
            _envelope_for(contact, "ZZT-msg-queue-timeout"), session_factory=SessionLocal
        )

        assert result.status != "failed", (result.status, result.stage)
        assert engine_mod.GENERIC_ERROR_REPLY not in [
            a.get("text") for a in result.actions if a.get("kind") == "send_message"
        ]
        row = SessionLocal().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        assert row is not None
        queued = [r for r in (row.trace or []) if r.get("stage") == "queued"]
        assert queued and queued[0]["facts"].get("timed_out") is True, queued
        # Ticket 2 finished but ticket 1 is still alive: `done` must not pass ticket 1.
        assert redis_client.get(_done_key(contact)) in (None, "0")
        redis_client.delete(dispatch.alive_key(contact, 1))


class TestFailureReleasesOrdering:
    """AC-704. A mid-turn exception must still release the ticket (`finally` semantics)."""

    def test_failure_releases_ordering(
        self, real_contacts, stub_engine_seams, stub_parser, monkeypatch
    ) -> None:
        _enable_ordering(monkeypatch)
        contact = real_contacts("fail-release")

        stub_parser()
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("access service down")),
        )

        first = engine_mod.run_turn(
            _envelope_for(contact, "ZZT-msg-fail-release-1"), session_factory=SessionLocal
        )
        assert first.status == "failed"

        client = _redis_client()
        try:
            done = client.get(_done_key(contact))
            assert done == "1", (
                "ticket 1 must be marked done even though the turn failed, or every "
                f"later ticket for this contact deadlocks forever; got done={done!r}"
            )
        finally:
            client.close()

        # The next ticket for the SAME contact must proceed without waiting out the
        # whole queue-wait timeout - it only needs done to have advanced.
        started = time.monotonic()
        second = engine_mod.run_turn(
            _envelope_for(contact, "ZZT-msg-fail-release-2"), session_factory=SessionLocal
        )
        elapsed = time.monotonic() - started
        assert second.status == "failed"  # check_access is still broken
        assert elapsed < 5.0, f"second ticket waited {elapsed}s - ticket 1 was never released"


class TestWorkerOffloadFlag:
    """AC-703. `chat` queue offload, optional, default off."""

    def test_flag_off_runs_in_process_no_enqueue(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        assert settings.chatbot_turn_on_worker is False

        called = {"count": 0}
        monkeypatch.setattr(
            engine_mod,
            "enqueue_job",
            lambda *a, **k: called.__setitem__("count", called["count"] + 1),
            raising=False,
        )
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert called["count"] == 0, "the flag is off - enqueue_job must never be called"
        assert result.branch_kind == "business_query"

    def test_flag_on_enqueues_on_chat_and_returns_the_job_result(
        self, session_factory, seeded, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings, "chatbot_turn_on_worker", True, raising=False)
        monkeypatch.setattr(settings, "chatbot_turn_wait_seconds", 60, raising=False)

        canned_turn_id = str(uuid.uuid4())
        canned_result = {
            "turn_id": canned_turn_id,
            "ctx": {"contact": {"id": CONTACT_ID}, "text": {}, "session": {}, "parse": {}, "access": {}, "media": None},
            "item": {"branch_kind": "business_query", "allowed": True, "decision": "allow"},
            "branch_kind": "business_query",
            "delegate": "business_query",
            "delegate_payload": None,
            "reply": None,
            "actions": [{"kind": "send_message", "text": "hi", "quick_replies": [], "dry_run": False}],
            "session_patch": None,
            "duplicate": False,
            "status": "delegated",
            "stage": "routed",
            "error": None,
        }

        enqueue_calls: list[dict[str, Any]] = []

        class _FakeJob:
            id = "fake-job-id"

        def fake_enqueue_job(*args, **kwargs):
            enqueue_calls.append(kwargs)
            return _FakeJob()

        # `raising=False`: `engine.py` may not import `enqueue_job` at module level yet
        # (it does not exist there today - this whole test is RED on that alone until
        # the coder wires the media.py-style module-level import in).
        monkeypatch.setattr(engine_mod, "enqueue_job", fake_enqueue_job, raising=False)
        monkeypatch.setattr(
            engine_mod,
            "get_job_status",
            lambda job_id: {"status": "finished", "result": canned_result, "exc_info": None},
            raising=False,
        )

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert enqueue_calls, "chatbot_turn_on_worker=True must enqueue, not run in-process"
        assert enqueue_calls[0].get("queue_name") == "chat", (
            f"must enqueue on the 'chat' queue, got kwargs={enqueue_calls[0]!r}"
        )
        assert result.turn_id == canned_turn_id
        assert result.status == "delegated"
        assert result.actions == canned_result["actions"]


class TestPollerBatchAndLiveMessageKeepOrder:
    """AC-713 / D15. A poller batch plus one interleaved webhook message, same contact."""

    def test_poller_batch_and_live_message_keep_order(
        self, real_contacts, stub_engine_seams, monkeypatch
    ) -> None:
        _complete_in_the_crm(monkeypatch)
        _enable_ordering(monkeypatch, queue_wait_seconds=5.0)
        contact = real_contacts("batch-order")

        ingresses = ["poller", "poller", "webhook", "poller", "poller", "poller"]
        results = []
        started = time.monotonic()
        for i, ingress in enumerate(ingresses, start=1):
            result = engine_mod.run_turn(
                _envelope_for(contact, f"ZZT-msg-batch-order-{i}", ingress=ingress),
                session_factory=SessionLocal,
            )
            results.append(result)
        elapsed = time.monotonic() - started

        assert elapsed < 5.0, (
            f"six sequential same-contact turns took {elapsed}s - something is waiting "
            "on a ticket that should already be free"
        )
        # `done`, because the lane is switched on and the CRM finishes it: this is the
        # shape S7 mode is FOR. It used to be `failed` here, forced by the contract rather
        # than by this test's subject - in S7 mode the CRM owns the tail (`/complete`
        # answers 410), so a turn routed to a lane the CRM cannot complete has nobody to
        # finish it and is closed `failed` at `routed` instead of left as a ghost
        # (`TestS7ModeRefusesADelegatingLane` below, which still grades that arm). With
        # S6c merged the lane CAN be switched on, so the six turns are answered end to
        # end and the ordering is measured over real work rather than over a refusal.
        assert [r.status for r in results] == ["done"] * 6, [r.error for r in results]
        assert all(r.delegate is None for r in results)
        assert all(r.reply and r.reply.get("text") for r in results)

        client = _redis_client()
        try:
            assert client.get(_seq_key(contact)) == "6"
            assert client.get(_done_key(contact)) == "6"
        finally:
            client.close()

        rows = {
            row.id: row
            for row in SessionLocal()
            .query(ChatbotTurn)
            .filter(ChatbotTurn.contact_respond_id == contact)
            .all()
        }
        by_created = sorted(rows.values(), key=lambda r: r.created_at)
        assert [r.ingress for r in by_created] == ingresses, (
            f"trace rows must carry the ingress each turn actually arrived through, "
            f"got {[r.ingress for r in by_created]}"
        )


class TestEmptyOrMalformedEnvelopeIsExplicit422:
    """H12: n8n's old dispatcher treated an empty pop as silent success. The CRM's version
    of that hazard is a caller that omits the inner message entirely and gets a phantom
    'success' with nothing understood - it must be an explicit, legible 422 instead, same
    as `test_engine_failure_paths.py::TestEnvelopeValidation`'s `contact.id` case."""

    def test_a_turn_request_with_no_message_key_is_refused_at_the_schema(self) -> None:
        with pytest.raises(pydantic.ValidationError) as excinfo:
            TurnRequest(envelope={"contact": {"id": "ZZT-contact-s7-h12"}})
        errors = excinfo.value.errors()
        assert any("message" in ".".join(str(p) for p in e["loc"]) for e in errors), errors

    def test_the_endpoint_answers_422_naming_message_not_a_500(self) -> None:
        api = FastAPI()

        @api.post("/turn")
        def turn(payload: TurnRequest):  # pragma: no cover - never reached
            return {"ok": True}

        client = TestClient(api, raise_server_exceptions=False)
        response = client.post(
            "/turn", json={"envelope": {"contact": {"id": "ZZT-contact-s7-h12"}}}
        )
        assert response.status_code == 422, response.text
        assert "message" in response.text


class TestS7ModeRefusesADelegatingLane:
    """The other half of the S7-mode contract: if the CRM owns the tail, nothing may be
    left waiting for one.

    `/complete` answers 410 in S7 mode, so a turn the head routes to a lane the CRM cannot
    finish has nobody to compose its reply. Left `delegated` it would sit as a ghost until
    the TTL sweep - ten minutes of a customer waiting for a reply no process is going to
    write. It is closed `failed` at the stage it reached instead, with the reason an
    operator can act on, and R4's manual Retry then works once the lane is switched on.

    This is a MISCONFIGURATION guard: the promote precondition (every lane in
    `system_settings.chatbot_completed_lanes`, on a build that can complete it) is written
    in `app/config.py` next to the flag.

    Ported (AC-1592): `chatbot_completed_lanes`/`_enabled_lanes` no longer gates
    completion at all (contract 73 superseded) - `completes_here = branch_kind in
    CRM_COMPLETED_BRANCH_KINDS` alone decides, and that frozenset covers all 13
    `BRANCH_KINDS` today, so `business_query` (and every other real branch kind) always
    completes in-process now; monkeypatching `_enabled_lanes` to empty (this class's own
    prior attempt to account for S6c) no longer reaches this guard at all, since nothing
    reads it for that decision anymore (measured: `_nothing_enabled` alone left both
    cells `"done"`, never `"failed"`/`"delegated"`). This class's own subject - the S7
    orphan guard, `delegate is not None and s7_mode` - is genuinely UNREACHABLE via any
    real branch kind today; it stays live code for a future 14th branch kind or a
    rollback build with a narrower `CRM_COMPLETED_BRANCH_KINDS` (AC-1507), so both tests
    below force THAT frozenset empty directly - the same seam `test_s3_switch_and_
    complete_by_body.py::delegated_turn` uses for the identical reason - to reach it.
    """

    @staticmethod
    def _nothing_enabled(monkeypatch) -> None:
        monkeypatch.setattr(engine_mod, "_enabled_lanes", lambda db, row=None: frozenset())
        monkeypatch.setattr(engine_mod, "CRM_COMPLETED_BRANCH_KINDS", frozenset())

    def test_s7_mode_fails_a_lane_the_crm_cannot_complete(
        self, real_contacts, stub_engine_seams, monkeypatch
    ) -> None:
        _enable_ordering(monkeypatch)
        self._nothing_enabled(monkeypatch)
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())
        contact = real_contacts("s7-orphan")

        result = engine_mod.run_turn(
            _envelope_for(contact, "ZZT-msg-s7-orphan-1"), session_factory=SessionLocal
        )

        assert result.status == "failed"
        assert result.stage == "routed"
        assert result.delegate is None, "S7 mode must not hand the caller a lane to run"
        # The customer still gets today's error reply to send, like every other failure.
        assert [a["kind"] for a in result.actions] == ["send_message"]
        assert result.actions[0]["text"] == engine_mod.GENERIC_ERROR_REPLY
        # And the operator gets the two facts that let them fix it: which lane, and where
        # to switch it on.
        assert "business_query" in (result.error or "")
        assert "chatbot_completed_lanes" in (result.error or "")

        db = SessionLocal()
        try:
            row = db.query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
            assert row is not None
            assert row.status == "failed"
            assert row.stage == "routed"
            # The row keeps the lane it was routed to - the routing was correct, the
            # configuration was not - and the trace carries the reason.
            assert row.branch_kind == "business_query"
            assert any(
                r.get("status") == "failed" and "chatbot_completed_lanes" in (r.get("error") or "")
                for r in (row.trace or [])
            ), row.trace
        finally:
            db.close()

    def test_with_the_flag_off_the_same_turn_still_delegates(
        self, real_contacts, stub_engine_seams, monkeypatch
    ) -> None:
        """The switch off is production today: the lane delegates and n8n completes it."""
        monkeypatch.setattr(engine_mod, "_s7_mode", lambda *args, **kwargs: False)
        self._nothing_enabled(monkeypatch)
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())
        contact = real_contacts("s7-orphan-off")

        result = engine_mod.run_turn(
            _envelope_for(contact, "ZZT-msg-s7-orphan-off-1"), session_factory=SessionLocal
        )

        assert result.status == "delegated"
        assert result.delegate == "business_query"
        assert result.error is None


class TestSingleTrigger:
    """H6, AC-701: in S7 mode the thin spine has exactly ONE trigger.

    **Adjusted from a route-inventory check after the captain's ruling of 5 Sep 2026.**
    The original assertion (`post_routes == ["/turn"]`) is the right end state and the
    wrong gate for S7: n8n's S2 tail keeps calling `POST /turn/{turn_id}/complete` until
    the S7 promote lands on the n8n side, so DELETING the route here would strand every
    turn a lane still completes. The physical deletion is S8 (plan, S8 slice;
    `documentation/plans/chatbot/n8n-changes.md`, S7 "Not covered by this slice").

    What S7 owes instead, and what this asserts: with the S7 mode flag on
    (`system_settings.chatbot_ordering_enabled`, the same switch that turns per-contact
    ordering on, because
    it is the same promote) `/turn` runs the whole turn and returns the finished reply and
    actions, and every `/complete` variant answers 410 Gone naming S7 mode. With the flag
    off - the default, and what production runs today - nothing changes.
    """

    _DONE_TURN = {
        "turn_id": "22222222-2222-2222-2222-222222222222",
        "is_test": False,
        "ctx": {"contact": {"id": CONTACT_ID}, "text": {}, "session": {}, "parse": {}, "access": {}, "media": None},
        "item": {"branch_kind": "low_signal", "allowed": True, "decision": "allow"},
        "branch_kind": "low_signal",
        "delegate": None,
        "reply": {"text": "Sure - which product do you mean?", "quick_replies": []},
        "actions": [
            {"kind": "send_message", "text": "Sure - which product do you mean?", "dry_run": False}
        ],
        "session_patch": None,
        "delegate_payload": None,
        "duplicate": False,
    }

    @pytest.fixture()
    def api(self, session_factory, monkeypatch):
        """The chat router alone, with the auth principal and the request session stubbed.

        Mounted bare rather than through `app.main` on purpose: what is under test is the
        ROUTER's own surface - which triggers it exposes and what each answers - and the
        two dependencies below are the only ones the routes declare themselves.
        """
        from fastapi import FastAPI as _FastAPI

        from app.api.v1.external import chat as chat_router_mod
        from app.dependencies import get_db, get_external_api_user

        class _DoneResult:
            def as_dict(self):
                return dict(TestSingleTrigger._DONE_TURN)

            status = "done"
            error = None

        class _CompletedResult:
            def as_dict(self):
                return {
                    "turn_id": TestSingleTrigger._DONE_TURN["turn_id"],
                    "reply": TestSingleTrigger._DONE_TURN["reply"],
                    "actions": TestSingleTrigger._DONE_TURN["actions"],
                    "session_patch": None,
                }

        monkeypatch.setattr(chat_router_mod, "run_turn", lambda *a, **k: _DoneResult())
        monkeypatch.setattr(
            chat_router_mod, "complete_turn", lambda *a, **k: _CompletedResult()
        )

        api = _FastAPI()
        api.include_router(chat_router_mod.router, prefix="/chat")

        def _override_db():
            db = session_factory()
            try:
                yield db
            finally:
                db.close()

        api.dependency_overrides[get_db] = _override_db
        api.dependency_overrides[get_external_api_user] = lambda: {"id": "test-principal"}
        return TestClient(api, raise_server_exceptions=False)

    def test_only_one_route_creates_a_turn(self) -> None:
        """The inventory half: `/complete` may exist, but only `/turn` starts a turn."""
        from app.api.v1.external import chat as chat_router_mod

        post_routes = [
            route.path
            for route in chat_router_mod.router.routes
            if "POST" in getattr(route, "methods", set())
        ]
        assert "/turn" in post_routes
        creating = [path for path in post_routes if not path.endswith("/complete")]
        assert creating == ["/turn"], (
            f"the thin spine must have exactly one turn-creating trigger, found {creating}"
        )

    def test_s7_mode_turn_returns_the_finished_reply_and_complete_is_gone(
        self, api, session_factory
    ) -> None:
        set_chatbot_switches(session_factory, ordering=True)

        turn = api.post(
            "/chat/turn",
            json={"envelope": {"message": {}, "contact": {"id": CONTACT_ID}}},
        )
        assert turn.status_code == 200, turn.text
        body = turn.json()
        assert body["delegate"] is None, "S7 mode delegates nothing - the CRM owns the tail"
        assert body["reply"]["text"], "the caller must get the finished reply to send"
        assert body["actions"], "and the actions to execute"

        gone = api.post(
            f"/chat/turn/{body['turn_id']}/complete", json={"item": {"branch_kind": "low_signal"}}
        )
        assert gone.status_code == 410, gone.text
        assert "S7 mode" in gone.text, gone.text

    def test_with_the_flag_off_complete_still_works(self, api, session_factory) -> None:
        set_chatbot_switches(session_factory, ordering=False)

        completed = api.post(
            f"/chat/turn/{TestSingleTrigger._DONE_TURN['turn_id']}/complete",
            json={"item": {"branch_kind": "business_query"}},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["reply"]["text"]
