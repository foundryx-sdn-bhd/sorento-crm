"""S6c + S7 end-to-end integration: the three seams the S6c/S7 integrator asked to be
graded together, all through `engine.run_turn` on the Postgres blank-schema fixture
(`tests/chatbot/conftest.py`'s `session_factory`), parser mocked via `stub_parser` /
`_parser_output` and MCP/LLM seams stubbed the same way `test_s6c_engine_paths.py` and
`test_s7_ordering_and_offload.py` do. Real, reachable redis
(`app.services.queue_service.redis_conn`) for every ordering assertion - nothing here
fakes `dispatch`.

1. **The `_run_stages` exit matrix (AC-715).** A `business_query` turn against all four
   combinations of `chatbot_completed_lanes` containing `business_query` and
   `system_settings.chatbot_business_lane_enabled`, crossed with
   `system_settings.chatbot_ordering_enabled` on and off -
   eight cells. Exactly one of three outcomes per cell: `done` with a real reply (both
   switches on, the CRM answers - AC-604's zero-tool wiring gives the fastest real
   in-CRM answer, the miss lane's `not_found`), `delegated` with `delegate` set (ordering
   off - n8n still owns the tail), or `failed` with the generic reply and an error naming
   `chatbot_completed_lanes` (ordering on, the lane not completable - AC-715's own
   subject). A dry run is the one documented exception (AC-715's own carve-out): ordering
   on and a lane the CRM cannot complete still delegates, with a `skipped` trace note
   rather than a failure, because a harness turn has no customer waiting and nothing was
   going to complete it either way. `CHATBOT_TURN_ON_WORKER` (AC-703) stays at its default
   False throughout this file - offload is a different axis and asserting it off keeps
   every turn here running in-process, in the same request `dispatch` and `run_turn`
   read.

2. **`_send_actions` on a CRM-completed business answer (AC-507, D15).** A `business_query`
   turn the CRM answers end to end (real resolve+gate bundle, a stubbed `run_fetch` result
   with `has_result: True`, which is `answer.dispatch`'s only gate into `sub_answer` -
   `answer.py:67-79` - and it is `sub_answer.central_exchange`'s own unwrap of the whole
   answered item, not a hand-built attachment list, that lands on `reply.attachments_src`
   whenever that arm runs, per `engine._attachments_src`'s docstring). Actions come out
   `[send_message, send_attachments]`, `send_attachments.reply` is the WHOLE sealed reply,
   `dry_run` is a real bool on both, `quick_replies` is a string or `None` never a list
   (AC-507's contract, the same one `_send_actions` and `_run_casual_lane` cite), and there
   is never a second `send_message`. The same turn replayed with the same `message_id`
   (D15) returns the identical `actions` list without calling `run_fetch` again.

3. **Ticket release under the S6c closes (AC-705).** With ordering on, a business turn
   whose fetch step raises (the outage close the S7-orphan-guard shares with a genuine
   lane crash) and one whose tail raises (the write-once terminal close `_close_turn`'s
   own docstring describes - `close_turn_for_tail` writes `delegated` first, the lane's own
   failure handler is the first TERMINAL write and therefore wins) both still release
   their contact's redis ticket in `run_turn`'s `finally`. A following message for the
   same contact does not wait out the queue budget, and `chatbot:done:{contact}` reaches
   `chatbot:seq:{contact}` afterwards - the same property AC-705's re-injected Retry
   depends on: a released ticket is what lets the re-posted original message take its
   place in the same per-contact order rather than deadlocking behind the turn it retries.

No em or en dashes. No regex over customer text.
"""
from __future__ import annotations

import time
from typing import Any

import pytest
import redis as redis_lib
from sqlalchemy import text

from app.config import settings
from app.models.chatbot_turn import ChatbotTurn
from app.models.user import SystemSetting
from app.services.chatbot import dispatch, engine as engine_mod
from app.services.chatbot.lanes.business.services import FetchServices
from tests.chatbot.conftest import set_chatbot_switches
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)
from tests.chatbot.test_s6c_answer_lane import stub_resolve_gate_bundle
from tests.chatbot.test_s6c_engine_paths import (
    _no_probe_answer_services,
    _srtwc8517_resolved_bundle,
)


def _redis_client():
    return redis_lib.from_url(settings.redis_url, decode_responses=True)


def _clear_contact_keys(client: Any, contact: str) -> None:
    client.delete(dispatch.seq_key(contact), dispatch.done_key(contact))
    for key in client.scan_iter(f"chatbot:alive:{contact}:*"):
        client.delete(key)


def _second_message_envelope(contact: str, message_id: str) -> Any:
    """A second, distinct message for the SAME contact - never a `run_turn(_envelope())`
    repeat, which D15 would read as a duplicate and answer without a second ticket."""
    return _envelope(
        message={
            "event_type": "message.received",
            "contact": {"id": contact},
            "message": {
                "messageId": message_id,
                "contactId": contact,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": "still there?"},
            },
        }
    )


def _empty_result_fetch_services() -> FetchServices:
    """AC-604: a read that finds nothing is an OUTCOME, not an empty turn.

    It used to stub `tool_search` to `[]` so no tool was picked at all. Since the tool RAG
    was dropped the domain names the tool (`forms` -> `crm_forms_management_forms_list`),
    so the empty answer is produced where it is actually produced in production: by the
    tool. The customer-facing outcome every cell below grades is the same one.
    """

    def _mcp_call(name: str, args: dict) -> Any:
        assert name == "crm_forms_management_forms_list", name
        return '{"answers": [], "has_result": false}'

    return FetchServices(mcp_call=_mcp_call)


@pytest.fixture()
def redis_client():
    client = _redis_client()
    yield client
    client.close()


def _wire_business_lane(engine_mod_ref: Any, monkeypatch: Any) -> None:
    """The bundle every matrix cell wires when `chatbot_business_lane_enabled` is on: a
    resolved-empty gate bundle plus a fetch whose tool answers empty, so the shadow run
    (lane on, arm not in `chatbot_completed_lanes`) and the CRM-answered run (both
    switches on) reach the SAME `not_found` outcome through `run_until_exit` +
    `run_fetch`, run for real - the only thing standing in for the network is the MCP
    seam and the two probes `_no_probe_answer_services` names as unreached on this arm."""
    bundle = stub_resolve_gate_bundle([])
    monkeypatch.setattr(
        engine_mod_ref.business_services, "production_services", lambda db, *, space_id=None: bundle
    )
    monkeypatch.setattr(
        engine_mod_ref.business_services, "fetch_services", lambda db: _empty_result_fetch_services()
    )
    monkeypatch.setattr(
        engine_mod_ref.business_services,
        "answer_services_for",
        lambda session_factory: _no_probe_answer_services(),
    )


def _set_completed_lanes(session_factory: Any, system_settings_row: Any, lanes: list[str]) -> None:
    db = session_factory()
    row = db.query(SystemSetting).filter(SystemSetting.id == system_settings_row.id).one()
    row.chatbot_completed_lanes = lanes
    db.commit()


# --------------------------------------------------------------------------- #
# 1. The `_run_stages` exit matrix (AC-715, AC-604's zero-tool wiring, AC-703 offload off)
# --------------------------------------------------------------------------- #


class TestBusinessQueryExitMatrixAc715:
    """AC-715: three outcomes, eight cells, never a fourth.

    Ported (AC-1592, this session): the matrix's own premise - `crm_completes = lane_in_
    settings and business_on` - is stale. `business_query` is in `CRM_COMPLETED_BRANCH_
    KINDS` unconditionally (S6c reached full coverage), `chatbot_completed_lanes` no
    longer gates completion at all (contract 73 superseded), and `chatbot_business_lane_
    enabled` is defined but never read from `run_turn` (grep-confirmed) - so all EIGHT
    cells complete `done` today, regardless of every switch combination. The matrix has
    collapsed to one outcome; the other two arms (`ordering_on` orphan-failure,
    `else` delegated) are dead code paths no real branch kind can reach anymore, since
    nothing is ever left un-completable for `run_turn` to hand off or fail on their
    behalf. `test_s3_switch_and_complete_by_body.py::TestTheCompletedLaneSwitch`'s own
    retirement note makes the identical finding independently.
    """

    _CELLS = [
        pytest.param(True, True, True, id="lane_on-business_on-ordering_on"),
        pytest.param(True, True, False, id="lane_on-business_on-ordering_off"),
        pytest.param(True, False, True, id="lane_on-business_off-ordering_on"),
        pytest.param(True, False, False, id="lane_on-business_off-ordering_off"),
        pytest.param(False, True, True, id="lane_off-business_on-ordering_on"),
        pytest.param(False, True, False, id="lane_off-business_on-ordering_off"),
        pytest.param(False, False, True, id="lane_off-business_off-ordering_on"),
        pytest.param(False, False, False, id="lane_off-business_off-ordering_off"),
    ]

    @pytest.mark.parametrize("lane_in_settings,business_on,ordering_on", _CELLS)
    def test_exit_matrix_cell_ac715(
        self,
        session_factory,
        seeded,
        stub_parser,
        stub_access,
        system_settings_row,
        monkeypatch,
        redis_client,
        lane_in_settings,
        business_on,
        ordering_on,
    ) -> None:
        assert settings.chatbot_turn_on_worker is False, (
            "AC-703 default: every cell here runs in-process, never offloaded"
        )
        _clear_contact_keys(redis_client, CONTACT_ID)

        _set_completed_lanes(
            session_factory, system_settings_row, ["business_query"] if lane_in_settings else []
        )
        set_chatbot_switches(session_factory, business_lane=business_on, ordering=ordering_on)
        monkeypatch.setattr(settings, "chatbot_queue_wait_seconds", 5.0, raising=False)
        _wire_business_lane(engine_mod, monkeypatch)

        stub_parser(_parser_output(domain_hint="forms", entities=[], user_goal="checking a form"))
        stub_access()

        try:
            result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

            # Every cell completes `done` today - see the class docstring for the
            # measured reason. `lane_in_settings`/`business_on`/`ordering_on` are kept as
            # parametrize inputs (rather than collapsing to a single un-parametrised
            # test) so a FUTURE regression that makes any one of them matter again shows
            # up as a cell-specific failure, not a single aggregate one.
            assert result.status == "done", (result.status, result.error)
            assert result.delegate is None
            assert [a["kind"] for a in result.actions] == ["send_message"]
            assert isinstance(result.reply.get("text"), str) and result.reply["text"], (
                "never done with no reply"
            )
            assert not (ordering_on and result.status == "delegated"), (
                "a live turn must never delegate while S7 mode is on"
            )
        finally:
            _clear_contact_keys(redis_client, CONTACT_ID)

    # RETIRED (AC-1592, this session): `test_a_dry_run_with_ordering_on_still_delegates_
    # with_a_skipped_trace_note_ac715` pinned the "documented exception" for a dry run
    # that could not complete either way - moot now that EVERY cell above completes
    # `done` (a dry run behaves identically to a live turn for the completion DECISION;
    # only the write is gated, D14). Measured: a dry-run `business_query` turn with the
    # SAME setup also reaches `status="done"` with a computed `session_patch` preview,
    # same as the live cells. Replacement coverage for D14's own "no write on a dry run"
    # guarantee already exists and is unaffected by this retirement:
    # `test_engine.py::TestDryRun.test_a_test_envelope_writes_nothing_outside_chatbot_
    # turns`. No new test needed here - re-deriving one would duplicate that file.


# --------------------------------------------------------------------------- #
# 2. `_send_actions` on a CRM-completed answer, and its D15 replay (AC-507, D15)
# --------------------------------------------------------------------------- #


def _wire_answered_business_turn(monkeypatch: Any, *, on_fetch: Any = None) -> dict[str, Any]:
    """A `business_query` turn the CRM answers all the way through the composer.

    Ported (AC-1592): `outcome_fragment['central-exchange']` is the OLD n8n-node-named
    read this docstring used to cite; the CURRENT read is `turn_runtime.envelope_of`,
    `files = fetched.get("attachments")` (grep-confirmed, `turn_runtime.py`) - a fetch
    fragment's `"fetch"` dict needs an `"attachments"` key (a list of dicts), which the
    old `answers` shape below never carried, so no `send_attachments` action was ever
    possible to reach through it.
    """
    from app.services.chatbot import engine as engine_ref

    call_count = {"run_fetch": 0}
    answers = {
        "answers": [{"product": "SRTWC8517", "stock_qty": 2}],
        "response": "2 in stock",
        "has_result": True,
        "attachments": [{"url": "s3://zzt/spec.pdf", "filename": "spec.pdf"}],
    }
    bundle = _srtwc8517_resolved_bundle()
    monkeypatch.setattr(
        engine_ref.business_services, "production_services", lambda db, *, space_id=None: bundle
    )
    monkeypatch.setattr(
        engine_ref.business_services, "answer_services_for", lambda sf: _no_probe_answer_services()
    )

    def _run_fetch(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        call_count["run_fetch"] += 1
        if on_fetch is not None:
            on_fetch()
        return {
            "kind": "result",
            "_fetch_arm": "result",
            "delegate": "business_query",
            "delegate_payload": {**payload, "fetch": {**answers, "_fetch_arm": "result"}},
            "fetch": {**answers, "_fetch_arm": "result"},
        }

    monkeypatch.setattr(engine_ref.business, "run_fetch", _run_fetch)
    return call_count


class TestSendActionsAttachmentsAndDuplicateAc507D15:
    """AC-507 (`quick_replies` a string or `None`, never a list) plus D15 (a duplicate
    replays the same `actions`, no second compose)."""

    @staticmethod
    def _prepare(session_factory, system_settings_row, stub_parser, stub_access, monkeypatch) -> dict[str, Any]:
        _set_completed_lanes(session_factory, system_settings_row, ["business_query"])
        set_chatbot_switches(session_factory, business_lane=True)
        call_count = _wire_answered_business_turn(monkeypatch)
        stub_parser(
            _parser_output(
                domain_hint="inventory",
                entities=[{"raw": "SRTWC8517", "hint": "product", "current_message": True}],
            )
        )
        stub_access()
        return call_count

    def test_a_crm_completed_business_answer_sends_the_message_then_the_attachments_ac507(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        self._prepare(session_factory, system_settings_row, stub_parser, stub_access, monkeypatch)

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.status == "done", result.error
        kinds = [a["kind"] for a in result.actions]
        assert kinds == ["send_message", "send_attachments"], kinds
        send, attach = result.actions

        assert isinstance(send.get("dry_run"), bool)
        assert isinstance(attach.get("dry_run"), bool)
        # AC-507: never a list, only a comma-joined string or null.
        assert send.get("quick_replies") is None or isinstance(send["quick_replies"], str)

        assert attach.get("attachments_src") is not None
        # Ported (AC-1592): the composed-answer path builds its actions through
        # `engine._answer_actions`, not `_send_actions` (the canned-lane builder) - its
        # `send_attachments` shape is `{kind, attachments_src, dry_run}` only, no `reply`
        # key (measured, grep-confirmed in `engine.py`). The property that matters - the
        # SAME files the reply names are what gets attached - is what this checks
        # instead.
        assert attach["attachments_src"] == result.reply["attachments_src"]

        assert sum(1 for a in result.actions if a["kind"] == "send_message") == 1, (
            "never a second send_message"
        )

    def test_the_same_turn_replayed_is_a_duplicate_with_the_identical_actions_d15(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        call_count = self._prepare(
            session_factory, system_settings_row, stub_parser, stub_access, monkeypatch
        )
        envelope = _envelope()

        first = engine_mod.run_turn(envelope, session_factory=session_factory)
        second = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert first.status == "done"
        assert second.duplicate is True
        assert second.turn_id == first.turn_id
        assert second.actions == first.actions
        assert second.reply == first.reply
        # D15: the business lane's own fetch must not run a second time for a duplicate.
        assert call_count["run_fetch"] == 1


# --------------------------------------------------------------------------- #
# 3. Ticket release under the S6c closes (AC-705)
# --------------------------------------------------------------------------- #


class TestTicketReleaseUnderS6cClosesAc705:
    """Ordering on; a fetch-raise outage close and a tail-raise terminal close both
    release the contact's ticket in `run_turn`'s `finally`, so a following message (the
    same shape AC-705's re-injected Retry arrives as) never waits past it."""

    @staticmethod
    def _enable_ordering(
        session_factory: Any, monkeypatch: Any, *, queue_wait_seconds: float = 5.0
    ) -> None:
        set_chatbot_switches(session_factory, ordering=True)
        monkeypatch.setattr(settings, "chatbot_queue_wait_seconds", queue_wait_seconds, raising=False)

    @staticmethod
    def _second_turn_delegates_fast(session_factory, monkeypatch) -> tuple[Any, float]:
        """The follow-up message: business lane switched off so it cannot re-raise, only
        used to prove the ticket a moment earlier is free - `elapsed` is what AC-705's own
        re-injected Retry depends on staying small.

        `elapsed` is how long the follow-up's `wait_for_turn` blocked, NOT the whole turn:
        a released ticket returns from the wait on its first Redis read, while a ticket
        never released sits there for the whole `chatbot_queue_wait_seconds` budget (5 s
        here) and raises `QueueWait`. The turn's own parser + DB work is not part of that
        property - it took 3.2 s on a loaded four-worker xdist runner (CI run 36087026240)
        with ticket 1 already released, which the old whole-turn clock read as a deadlock.
        """
        set_chatbot_switches(session_factory, business_lane=False)
        real_wait = dispatch.wait_for_turn
        waited: list[float] = []

        def _timed_wait(*args: Any, **kwargs: Any) -> None:
            started = time.monotonic()
            try:
                real_wait(*args, **kwargs)
            finally:
                waited.append(time.monotonic() - started)

        monkeypatch.setattr(dispatch, "wait_for_turn", _timed_wait)
        result = engine_mod.run_turn(
            _second_message_envelope(CONTACT_ID, "ZZT-msg-s6s7-followup"),
            session_factory=session_factory,
        )
        assert waited, "ordering is on, so the follow-up must take a ticket and wait on it"
        return result, waited[0]

    def test_a_fetch_raise_outage_close_still_releases_the_ticket_ac705(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch, redis_client
    ) -> None:
        _clear_contact_keys(redis_client, CONTACT_ID)
        _set_completed_lanes(session_factory, system_settings_row, ["business_query"])
        set_chatbot_switches(session_factory, business_lane=True)
        self._enable_ordering(session_factory, monkeypatch)

        bundle = _srtwc8517_resolved_bundle()
        monkeypatch.setattr(
            engine_mod.business_services, "production_services", lambda db, *, space_id=None: bundle
        )
        monkeypatch.setattr(
            engine_mod.business_services, "answer_services_for", lambda sf: _no_probe_answer_services()
        )

        def _outage(payload: dict[str, Any], **kwargs: Any) -> Any:
            raise RuntimeError("mcp outage")

        monkeypatch.setattr(engine_mod.business, "run_fetch", _outage)
        stub_parser(
            _parser_output(
                domain_hint="inventory",
                entities=[{"raw": "SRTWC8517", "hint": "product", "current_message": True}],
            )
        )
        stub_access()

        try:
            first = engine_mod.run_turn(_envelope(), session_factory=session_factory)
            assert first.status == "failed"
            assert "mcp outage" in (first.error or "")

            assert redis_client.get(dispatch.seq_key(CONTACT_ID)) == "1"
            assert redis_client.get(dispatch.done_key(CONTACT_ID)) == "1", (
                "the outage close must release ticket 1 in run_turn's finally, or every "
                "later message for this contact deadlocks behind it"
            )
            assert redis_client.exists(dispatch.alive_key(CONTACT_ID, 1)) == 0

            second, elapsed = self._second_turn_delegates_fast(session_factory, monkeypatch)
            assert elapsed < 2.0, (
                f"the follow-up message waited {elapsed}s - ticket 1 was never released"
            )
            assert redis_client.get(dispatch.seq_key(CONTACT_ID)) == "2"
            assert redis_client.get(dispatch.done_key(CONTACT_ID)) == "2"
        finally:
            _clear_contact_keys(redis_client, CONTACT_ID)

    def test_a_tail_raise_terminal_close_still_releases_the_ticket_ac705(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch, redis_client
    ) -> None:
        """Ported (AC-1592): `engine.business.complete_answer` is the OLD "answer half"
        seam (S3 rewired the dispatcher; `turn_compose.compose` runs inside the SAME
        fetch try/except now, so raising there lands at `looked_up`, not a TAIL failure
        at all) - the attribute still exists on `business` but nothing calls it, so the
        old monkeypatch silently never fired (measured: `first.status` was `"done"`, not
        `"failed"`). The seam for a genuine TAIL failure (composed, then the WRITE
        breaks) is `turn_tail.persist` - called from `engine._run_answer` right after
        `stage[0] = "remembered"` (measured, not guessed), which is also why `row.stage`
        below is `"remembered"` now, not the old `"replied"`.
        """
        _clear_contact_keys(redis_client, CONTACT_ID)
        _set_completed_lanes(session_factory, system_settings_row, ["business_query"])
        set_chatbot_switches(session_factory, business_lane=True)
        self._enable_ordering(session_factory, monkeypatch)
        _wire_answered_business_turn(monkeypatch)

        def _boom_persist(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("tail exploded")

        monkeypatch.setattr(engine_mod.turn_tail, "persist", _boom_persist)
        stub_parser(
            _parser_output(
                domain_hint="inventory",
                entities=[{"raw": "SRTWC8517", "hint": "product", "current_message": True}],
            )
        )
        stub_access()

        try:
            first = engine_mod.run_turn(_envelope(), session_factory=session_factory)
            assert first.status == "failed"
            assert "tail exploded" in (first.error or "")

            db = session_factory()
            row = db.query(ChatbotTurn).filter(ChatbotTurn.id == first.turn_id).one()
            assert row.status == "failed"
            assert row.stage == "remembered"

            assert redis_client.get(dispatch.seq_key(CONTACT_ID)) == "1"
            assert redis_client.get(dispatch.done_key(CONTACT_ID)) == "1", (
                "a tail failure inside _run_business_answer must still release the "
                "ticket, or the contact is stuck behind a turn that already finished"
            )
            assert redis_client.exists(dispatch.alive_key(CONTACT_ID, 1)) == 0

            second, elapsed = self._second_turn_delegates_fast(session_factory, monkeypatch)
            assert elapsed < 2.0, (
                f"the follow-up message waited {elapsed}s - ticket 1 was never released"
            )
            assert redis_client.get(dispatch.seq_key(CONTACT_ID)) == "2"
            assert redis_client.get(dispatch.done_key(CONTACT_ID)) == "2"
        finally:
            _clear_contact_keys(redis_client, CONTACT_ID)
