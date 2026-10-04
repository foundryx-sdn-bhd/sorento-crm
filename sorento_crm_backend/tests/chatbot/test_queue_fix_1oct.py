"""CHATBOT-QUEUE-FIX: the per-contact queue, replayed against the prod sequence of 1 Oct.

Contact 900000081 (MYT): f0a2 ticket 1 done 10:05:35 -> 10:05:41; 65ce ticket 2 created
10:06:43 FAILED "QueueWait: waited 45.0s for ticket 1" 62 s after ticket 1 finished; a45f
ticket 3 ran 3m52s (the parser call); a1d7 ticket 4 FAILED waiting for ticket 3; and
4a87/b34b/9a2c ran while ticket 3 was still open (ticket 4's timeout had marked it done).

Prod redis afterwards: no eviction, no release errors, seq=11/done=11. f0a2 was "ticket 1,
wait 0", so `seq` had restarted after a quiet hour while the previous session's `done`
survived it: the two keys aged on separate clocks.

Every test here drives `dispatch` against the real redis this suite already uses, with the
liveness TTL shrunk so no test waits on wall-clock seconds.
"""
from __future__ import annotations

import time
import uuid

import pytest

from app.services.chatbot import dispatch
from tests.chatbot.test_s7_ordering_and_offload import _redis_client


@pytest.fixture()
def r():
    client = _redis_client()
    try:
        client.ping()
    except Exception:  # pragma: no cover - the suite's redis is a hard dependency
        pytest.skip("redis is not reachable")
    return client


@pytest.fixture()
def contact(r):
    c = f"ZZT-queue-1oct-{uuid.uuid4().hex[:8]}"
    yield c
    for key in r.scan_iter(f"chatbot:*:{c}*"):
        r.delete(key)


@pytest.fixture()
def fast(monkeypatch):
    """Liveness in tenths of a second, so a dead turn lapses inside a test."""
    monkeypatch.setattr(dispatch, "ALIVE_TTL_SECONDS", 0.4, raising=False)
    monkeypatch.setattr(dispatch, "HEARTBEAT_INTERVAL_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(dispatch, "POLL_INTERVAL_SECONDS", 0.02, raising=False)


def _timed_wait(r, contact, ticket, timeout_s):
    started = time.monotonic()
    dispatch.wait_for_turn(r, contact, ticket, timeout_s=timeout_s)
    return time.monotonic() - started


def test_prod_f0a2_65ce_stale_done_from_an_earlier_session(r, contact, fast):
    """seq expired, done=10 from the earlier session survived it. Ticket 1 runs and
    releases, the stale done then lapses, and ticket 2 must NOT wait for ticket 1."""
    r.set(dispatch.done_key(contact), 10, ex=30)
    assert r.get(dispatch.seq_key(contact)) is None

    t1 = dispatch.contact_ticket(r, contact)
    assert t1 == 1
    dispatch.wait_for_turn(r, contact, t1, timeout_s=1.0)
    dispatch.mark_done(r, contact, t1)
    # The old `done` would now lapse on its own clock; model that.
    if r.get(dispatch.done_key(contact)) == "10":
        r.delete(dispatch.done_key(contact))

    t2 = dispatch.contact_ticket(r, contact)
    assert t2 == 2
    assert _timed_wait(r, contact, t2, timeout_s=2.0) < 0.5
    assert r.get(dispatch.done_key(contact)) == "1"


def test_take_resets_a_done_from_an_earlier_session(r, contact):
    r.set(dispatch.done_key(contact), 10, ex=30)
    ticket = dispatch.contact_ticket(r, contact)
    assert ticket == 1
    assert r.get(dispatch.done_key(contact)) == "0"


def test_a_waiter_never_reads_its_own_stamp_as_a_live_predecessor(r, contact, fast):
    """Ticket 1's process died without releasing. Ticket 2's own take must not keep the
    contact looking busy: it proceeds once ticket 1's liveness lapses, not after the cap."""
    dispatch.contact_ticket(r, contact)  # ticket 1, no heartbeat, never released
    t2 = dispatch.contact_ticket(r, contact)
    hb = dispatch.start_heartbeat(r, contact, t2)
    try:
        elapsed = _timed_wait(r, contact, t2, timeout_s=3.0)
    finally:
        hb.stop()
    assert elapsed < 1.5


def test_a_live_predecessor_is_waited_for_until_it_releases(r, contact, fast):
    t1 = dispatch.contact_ticket(r, contact)
    hb1 = dispatch.start_heartbeat(r, contact, t1)
    t2 = dispatch.contact_ticket(r, contact)
    hb2 = dispatch.start_heartbeat(r, contact, t2)
    try:
        # Well past the 0.4 s liveness TTL: only the heartbeat keeps ticket 1 alive.
        with pytest.raises(dispatch.QueueWait):
            dispatch.wait_for_turn(r, contact, t2, timeout_s=1.0)
        hb1.stop()
        dispatch.mark_done(r, contact, t1)
        assert _timed_wait(r, contact, t2, timeout_s=1.0) < 0.3
    finally:
        hb1.stop()
        hb2.stop()


def test_prod_a1d7_timed_out_ticket_never_releases_past_a_live_predecessor(r, contact, fast):
    """Ticket 3 is slow and alive (a45f). Ticket 4 gives up waiting and runs anyway; its
    release must leave `done` at 2, so ticket 5 still waits for ticket 3."""
    dispatch.mark_done(r, contact, dispatch.contact_ticket(r, contact))  # 1
    dispatch.mark_done(r, contact, dispatch.contact_ticket(r, contact))  # 2
    t3 = dispatch.contact_ticket(r, contact)
    hb3 = dispatch.start_heartbeat(r, contact, t3)
    try:
        t4 = dispatch.contact_ticket(r, contact)
        with pytest.raises(dispatch.QueueWait):
            dispatch.wait_for_turn(r, contact, t4, timeout_s=0.5)
        dispatch.mark_done(r, contact, t4)  # ticket 4 ran past the cap and finished
        assert r.get(dispatch.done_key(contact)) == "2"

        t5 = dispatch.contact_ticket(r, contact)
        hb5 = dispatch.start_heartbeat(r, contact, t5)
        with pytest.raises(dispatch.QueueWait):
            dispatch.wait_for_turn(r, contact, t5, timeout_s=0.5)

        hb3.stop()
        dispatch.mark_done(r, contact, t3)
        # 3 and 4 are both finished now, so `done` walks to 4 and ticket 5 goes.
        assert _timed_wait(r, contact, t5, timeout_s=1.0) < 0.3
        assert r.get(dispatch.done_key(contact)) == "4"
        hb5.stop()
    finally:
        hb3.stop()


def test_a_lost_release_heals_once_the_heartbeat_stops(r, contact, fast):
    """The release write failed (redis blip) but the turn finished and stopped beating:
    the successor waits at most one liveness TTL, never the full cap."""
    t1 = dispatch.contact_ticket(r, contact)
    hb1 = dispatch.start_heartbeat(r, contact, t1)
    hb1.stop()  # finished; mark_done never lands
    t2 = dispatch.contact_ticket(r, contact)
    assert _timed_wait(r, contact, t2, timeout_s=3.0) < 1.0


def test_done_never_moves_backwards(r, contact):
    for _ in range(3):
        dispatch.mark_done(r, contact, dispatch.contact_ticket(r, contact))
    assert r.get(dispatch.done_key(contact)) == "3"
    dispatch.mark_done(r, contact, 1)
    assert r.get(dispatch.done_key(contact)) == "3"


def test_seq_and_done_age_together(r, contact):
    dispatch.mark_done(r, contact, dispatch.contact_ticket(r, contact))
    dispatch.contact_ticket(r, contact)
    seq_ttl = r.ttl(dispatch.seq_key(contact))
    done_ttl = r.ttl(dispatch.done_key(contact))
    assert seq_ttl > 0 and done_ttl > 0
    assert abs(seq_ttl - done_ttl) <= 1


def test_the_liveness_ttl_outlasts_one_slow_redis_call():
    from app.services.queue_service import REDIS_SOCKET_TIMEOUT_SECONDS

    assert dispatch.ALIVE_TTL_SECONDS > REDIS_SOCKET_TIMEOUT_SECONDS
    assert dispatch.ALIVE_TTL_SECONDS >= 3 * dispatch.HEARTBEAT_INTERVAL_SECONDS


# --------------------------------------------------------------------------- #
# The engine around the queue: release alert and per-stage timing (item 5)
# --------------------------------------------------------------------------- #

from tests.chatbot.test_engine import stub_parser  # noqa: E402,F401 - fixture used by name
from tests.chatbot.test_s7_ordering_and_offload import (  # noqa: E402,F401 - fixtures/helpers
    _enable_ordering,
    _envelope_for,
    real_contacts,
    redis_client,
    stub_engine_seams,
)


def test_a_failed_release_logs_at_error_naming_contact_and_ticket(
    real_contacts, stub_engine_seams, stub_parser, monkeypatch, caplog
):
    import logging

    from redis import exceptions as redis_exceptions

    from app.database import SessionLocal
    from app.services.chatbot import engine as engine_mod

    _enable_ordering(monkeypatch)
    stub_parser()
    contact = real_contacts("release-fails")

    def _boom(redis, c, ticket):
        raise redis_exceptions.ConnectionError("redis went away")

    monkeypatch.setattr(dispatch, "mark_done", _boom)
    with caplog.at_level(logging.ERROR, logger="app.services.chatbot.engine"):
        engine_mod.run_turn(_envelope_for(contact, "ZZT-msg-release-fails"), session_factory=SessionLocal)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "release" in r.getMessage()]
    assert errors, [r.getMessage() for r in caplog.records]
    assert contact in errors[0].getMessage()
    assert "ticket 1" in errors[0].getMessage()


def test_a_slow_turn_logs_every_stage_with_its_ms(
    real_contacts, stub_engine_seams, stub_parser, monkeypatch, caplog
):
    import logging

    from app.database import SessionLocal
    from app.services.chatbot import engine as engine_mod

    _enable_ordering(monkeypatch)
    stub_parser()
    monkeypatch.setattr(engine_mod, "SLOW_TURN_LOG_SECONDS", 0.0)
    contact = real_contacts("slow-turn")
    with caplog.at_level(logging.WARNING, logger="app.services.chatbot.engine"):
        engine_mod.run_turn(_envelope_for(contact, "ZZT-msg-slow-turn"), session_factory=SessionLocal)

    slow = [r.getMessage() for r in caplog.records if "slow turn" in r.getMessage()]
    assert slow, [r.getMessage() for r in caplog.records]
    assert contact in slow[0] and "understood=" in slow[0] and "ms" in slow[0]


def test_a_stale_done_equal_to_the_new_ticket_is_reset(r, contact, fast):
    """Review S4: a stale `done=1` survives a seq restart. Ticket 1 of the new session is
    alive, so ticket 2 must wait for it, not read the stale 1 as "ticket 1 finished"."""
    r.set(dispatch.done_key(contact), 1, ex=30)
    t1 = dispatch.contact_ticket(r, contact)
    hb1 = dispatch.start_heartbeat(r, contact, t1)
    t2 = dispatch.contact_ticket(r, contact)
    hb2 = dispatch.start_heartbeat(r, contact, t2)
    try:
        with pytest.raises(dispatch.QueueWait):
            dispatch.wait_for_turn(r, contact, t2, timeout_s=0.6)
    finally:
        hb1.stop()
        hb2.stop()


def test_a_hung_turn_stops_beating_after_the_max_hold(r, contact, fast, monkeypatch):
    """Review S1: a turn stuck in a blocking call must not keep its ticket alive forever."""
    monkeypatch.setattr(dispatch, "MAX_HOLD_SECONDS", 0.3)
    t1 = dispatch.contact_ticket(r, contact)
    hb1 = dispatch.start_heartbeat(r, contact, t1)  # never stopped: the turn is hung
    t2 = dispatch.contact_ticket(r, contact)
    hb2 = dispatch.start_heartbeat(r, contact, t2)
    try:
        # Lapses at roughly max hold + liveness TTL, well inside the cap.
        assert _timed_wait(r, contact, t2, timeout_s=3.0) < 2.0
    finally:
        hb1.stop()
        hb2.stop()


def _heartbeat_threads() -> list[str]:
    import threading

    return [t.name for t in threading.enumerate() if t.name.startswith("chatbot-heartbeat-")]


def test_run_turn_leaves_no_heartbeat_thread_behind(
    real_contacts, stub_engine_seams, stub_parser, monkeypatch
):
    """Review S3: the engine stops the heartbeat on the way out, success and failure."""
    from app.database import SessionLocal
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.head import parser as parser_mod

    _enable_ordering(monkeypatch)
    before = _heartbeat_threads()
    contact = real_contacts("no-leak")

    stub_parser()
    engine_mod.run_turn(_envelope_for(contact, "ZZT-msg-no-leak-1"), session_factory=SessionLocal)
    assert _heartbeat_threads() == before

    stub_parser(error=parser_mod.ParserError("boom"))
    failed = engine_mod.run_turn(
        _envelope_for(contact, "ZZT-msg-no-leak-2"), session_factory=SessionLocal
    )
    assert failed.status == "failed"
    assert _heartbeat_threads() == before

    # A duplicate delivery replays before any ticket is taken.
    engine_mod.run_turn(_envelope_for(contact, "ZZT-msg-no-leak-1"), session_factory=SessionLocal)
    assert _heartbeat_threads() == before


# --------------------------------------------------------------------------- #
# Crew decision (1 Oct): the reply is never lost, and a late turn merges its state
# --------------------------------------------------------------------------- #


def test_the_queue_wait_leaves_room_for_the_turn_inside_n8ns_budget():
    from app.config import settings
    from app.services.chatbot.llm_call import CALL_DEADLINE_SECONDS
    from app.services.chatbot.send_order import N8N_CHAT_TURN_TIMEOUT_SECONDS

    ceiling = dispatch.max_queue_wait_seconds()
    assert (
        ceiling + CALL_DEADLINE_SECONDS + dispatch.LANE_BUDGET_SECONDS + dispatch.QUEUE_MARGIN_SECONDS
        <= N8N_CHAT_TURN_TIMEOUT_SECONDS
    )
    assert ceiling == 30.0
    assert settings.chatbot_queue_wait_seconds <= ceiling
    assert dispatch.queue_wait_seconds(45.0) == ceiling, "an old env value is clamped"
    assert dispatch.queue_wait_seconds(10.0) == 10.0


def test_the_engine_waits_with_the_clamped_value(
    real_contacts, stub_engine_seams, stub_parser, monkeypatch
):
    from app.config import settings
    from app.database import SessionLocal
    from app.services.chatbot import engine as engine_mod

    _enable_ordering(monkeypatch, queue_wait_seconds=999.0)
    stub_parser()
    seen: list[float] = []
    monkeypatch.setattr(
        dispatch, "wait_for_turn", lambda redis, c, t, *, timeout_s: seen.append(timeout_s)
    )
    assert settings.chatbot_queue_wait_seconds == 999.0
    engine_mod.run_turn(
        _envelope_for(real_contacts("clamp"), "ZZT-msg-clamp"), session_factory=SessionLocal
    )
    assert seen == [dispatch.max_queue_wait_seconds()]


@pytest.fixture()
def state_contact():
    import json

    from sqlalchemy import text

    from app.database import SessionLocal

    cid = f"ZZT-merge-{uuid.uuid4().hex[:10]}"
    db = SessionLocal()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": cid, "phone": f"+6013{uuid.uuid4().hex[:8]}", "sv": json.dumps({})},
    )
    db.commit()
    yield db, cid
    db.rollback()
    db.execute(text("DELETE FROM integration_log WHERE external_reference = :cid"), {"cid": cid})
    db.execute(text("DELETE FROM respond_contacts WHERE respond_io_id = :cid"), {"cid": cid})
    db.commit()
    db.close()


def _five(focus=None, open_question=None):
    return {
        "focus": focus,
        "open_question": open_question,
        "ideation": None,
        "access_levels": [],
        "contains_flyer": False,
    }


def _stored(db, cid):
    from app.services.chatbot import session_state
    from app.services.conversation_variables_service import get_for_contact

    return session_state.five_keys({"session_vars": get_for_contact(db, respond_io_id=cid)})


def test_an_unchanged_state_is_written_exactly(state_contact):
    from app.services.chatbot.turn import tail
    from app.services.conversation_variables_service import overwrite_for_contact

    db, cid = state_contact
    start = _five(focus={"products": ["A"]})
    overwrite_for_contact(db, respond_io_id=cid, state=start)
    mine = _five(focus={"products": ["B"]}, open_question={"kind": "pick"})
    tail.write_merged(db, respond_io_id=cid, payload=mine, base=_stored(db, cid), turn_id="t2")
    assert _stored(db, cid) == mine


def test_a_late_turn_keeps_what_the_predecessor_wrote(state_contact, caplog):
    import logging

    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.turn import tail
    from app.services.conversation_variables_service import overwrite_for_contact

    db, cid = state_contact
    overwrite_for_contact(db, respond_io_id=cid, state=_five(focus={"products": ["A"]}))
    base = _stored(db, cid)  # the late turn reads here, then waits out the cap

    slow = str(uuid.uuid4())
    overwrite_for_contact(db, respond_io_id=cid, state=_five(focus={"products": ["SLOW"]}))
    engine_mod._log_session_write(db, turn_id=slow, contact_respond_id=cid)

    # The late turn only opened a question; it never touched focus.
    mine = {**base, "open_question": {"kind": "pick"}}
    with caplog.at_level(logging.WARNING, logger="app.services.chatbot.turn.tail"):
        tail.write_merged(db, respond_io_id=cid, payload=mine, base=base, turn_id="late")
    after = _stored(db, cid)
    assert after["focus"] == {"products": ["SLOW"]}, "the predecessor's focus survives"
    assert after["open_question"] == {"kind": "pick"}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_both_changed_is_last_write_wins_with_a_warning_naming_both_turns(state_contact, caplog):
    import logging

    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.turn import tail
    from app.services.conversation_variables_service import overwrite_for_contact

    db, cid = state_contact
    overwrite_for_contact(db, respond_io_id=cid, state=_five(focus={"products": ["A"]}))
    base = _stored(db, cid)
    slow = str(uuid.uuid4())
    overwrite_for_contact(db, respond_io_id=cid, state=_five(focus={"products": ["SLOW"]}))
    engine_mod._log_session_write(db, turn_id=slow, contact_respond_id=cid)

    late = str(uuid.uuid4())
    mine = {**base, "focus": {"products": ["LATE"]}}
    with caplog.at_level(logging.WARNING, logger="app.services.chatbot.turn.tail"):
        tail.write_merged(db, respond_io_id=cid, payload=mine, base=base, turn_id=late)
    assert _stored(db, cid)["focus"] == {"products": ["LATE"]}
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and late in warnings[0] and slow in warnings[0] and "focus" in warnings[0]


def test_a_late_turn_that_round_tripped_focus_still_keeps_the_predecessors(state_contact, caplog):
    """Review round 2 M1, the realistic shape: the late turn's focus went through
    `focus_from_wire` -> `focus_to_wire` (which down-flags `current_message`), so it is
    NOT byte-equal to what it read, yet it did not change focus. The slow turn's focus
    must survive, with no "both changed" warning."""
    import logging

    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.turn import tail
    from app.services.chatbot.turn.state import focus_from_wire, focus_to_wire
    from app.services.conversation_variables_service import overwrite_for_contact

    db, cid = state_contact
    named = {"products": [{"canonical_code": "AAA1", "current_message": True}]}
    overwrite_for_contact(db, respond_io_id=cid, state=_five(focus=named))
    base = _stored(db, cid)

    slow = str(uuid.uuid4())
    overwrite_for_contact(
        db,
        respond_io_id=cid,
        state=_five(focus={"products": [{"canonical_code": "SLOW3", "current_message": True}]}),
    )
    engine_mod._log_session_write(db, turn_id=slow, contact_respond_id=cid)

    round_tripped = focus_to_wire(focus_from_wire(base["focus"]))
    assert round_tripped != base["focus"], "the premise: loading is not lossless"
    mine = {**base, "focus": round_tripped, "open_question": {"kind": "pick"}}
    with caplog.at_level(logging.WARNING, logger="app.services.chatbot.turn.tail"):
        tail.write_merged(db, respond_io_id=cid, payload=mine, base=base, turn_id="late4")
    after = _stored(db, cid)
    assert after["focus"]["products"][0]["canonical_code"] == "SLOW3"
    assert after["open_question"] == {"kind": "pick"}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_engine_hands_its_base_to_the_merge(
    real_contacts, stub_engine_seams, monkeypatch
):
    """Review round 2 M2: a slow turn writes this contact's state while this turn is
    mid-flight (here: inside the parser call). A key this turn does not touch
    (`ideation`) must keep the other turn's write. With `base=None` it is erased."""
    import json

    from sqlalchemy import text

    from app.database import SessionLocal
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.head import parser as parser_mod
    from app.services.conversation_variables_service import get_for_contact
    from tests.chatbot.test_engine import _parser_output

    _enable_ordering(monkeypatch)
    contact = real_contacts("merge-engine")
    pointer = {"submission_id": "ZZT-slow-turn-draft", "status": "draft"}

    def parse_while_another_turn_writes(config, user_block):
        db = SessionLocal()
        try:
            stored = get_for_contact(db, respond_io_id=contact)
            db.execute(
                text("UPDATE respond_contacts SET session_vars = CAST(:s AS jsonb) WHERE respond_io_id = :c"),
                {"s": json.dumps({**stored, "ideation": pointer}), "c": contact},
            )
            db.commit()
        finally:
            db.close()
        return _parser_output()

    monkeypatch.setattr(parser_mod, "parse", parse_while_another_turn_writes)
    engine_mod.run_turn(_envelope_for(contact, "ZZT-msg-merge-engine"), session_factory=SessionLocal)

    db = SessionLocal()
    try:
        assert get_for_contact(db, respond_io_id=contact).get("ideation") == pointer
    finally:
        db.close()
