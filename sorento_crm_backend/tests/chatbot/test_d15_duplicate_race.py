"""D15: two near-simultaneous deliveries of the SAME respond `message_id` (the webhook
producer and the failover poller are two injectors of one envelope shape) must produce
exactly one `chatbot.turns` row and one turn's worth of side effects; the loser gets
`{duplicate: true}` back, not an error.

**Real, committed Postgres via `SessionLocal`** (not the blank-schema `session_factory`
used everywhere else in this suite) - the whole point is what a SECOND, genuinely
concurrent connection sees while the first is mid-transaction, which a single-connection
rollback-scoped fixture cannot show. Same pattern and same reasoning as
`tests/scm/test_spo_conversion.py::test_f3_concurrent_create_calls_on_the_same_shipment_are_serialised_by_the_row_lock`
and `tests/test_media_quota_serialization.py`. Marker-prefixed (`ZZT-`) and cleaned up by
hand in `finally`, since nothing here rolls back.

**Defect found here, then FIXED (S1 of the review).** `engine.run_turn`'s dedup was a
SELECT then, on a miss, an INSERT, with nothing catching the `IntegrityError` the unique
index raises when two deliveries genuinely race - so the losing thread got a 500 instead
of `{duplicate: true}`. Natural thread timing hides it (`SessionLocal`'s pool warm-up for
the second thread usually lets the first finish outright), which is why
`test_forced_toctou_window_between_the_select_and_the_insert` holds both threads at a
barrier INSIDE `_existing_turn` until both have missed. Both tests were `xfail` while the
defect stood; the engine now catches the collision, re-reads the winner's row through
`_select_turn` (deliberately NOT `_existing_turn`, or the retry would re-enter that
barrier) and replays its stored response, so the markers are gone and these are ordinary
passing tests.
"""
from __future__ import annotations

import json
import threading

import pytest
from sqlalchemy import text

from app.database import SessionLocal
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import Envelope
from app.services.chatbot.head import parser as parser_mod
from tests.chatbot.test_engine import _parser_output

CONTACT_ID = "ZZT-contact-900000d15"
MESSAGE_ID = "ZZT-msg-d15-race"


def _envelope() -> Envelope:
    return Envelope(
        contact={"id": CONTACT_ID, "firstName": "ZZT", "custom_fields": []},
        message={
            "event_type": "message.received",
            "contact": {"id": CONTACT_ID},
            "message": {
                "messageId": MESSAGE_ID,
                "contactId": CONTACT_ID,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": "price for SRTWC8517"},
            },
        },
    )


@pytest.fixture()
def real_db_contact():
    """Seeds ONE real, committed `respond_contacts` row and cleans up in `finally`."""
    seed_db = SessionLocal()
    try:
        seed_db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
                "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb)) "
                "ON CONFLICT DO NOTHING"
            ),
            {"cid": CONTACT_ID, "phone": "+60000000900", "sv": json.dumps({"variables": {}})},
        )
        seed_db.commit()
    finally:
        seed_db.close()

    yield

    cleanup = SessionLocal()
    try:
        cleanup.query(ChatbotTurn).filter(ChatbotTurn.contact_respond_id == CONTACT_ID).delete(
            synchronize_session=False
        )
        cleanup.execute(
            text("DELETE FROM respond_contacts WHERE respond_io_id = :cid"), {"cid": CONTACT_ID}
        )
        cleanup.commit()
    finally:
        cleanup.close()


@pytest.fixture()
def stub_engine_seams(monkeypatch):
    def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())
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


def test_two_threads_same_message_id_leave_exactly_one_turn_row(
    real_db_contact, stub_engine_seams
):
    """D15 / AC-712. `engine.run_turn`'s own dedup is a SELECT then, on a miss, an
    INSERT - the docstring on `ChatbotTurn` promises "the second insert collides ...
    duplicate: true", which reads as the unique constraint being the backstop under a
    genuine race. Nothing in `run_turn` catches `IntegrityError` around `_insert_turn`,
    so this test's job is to show whether that backstop actually holds or whether the
    loser gets an unhandled exception instead (which the endpoint's generic `except
    Exception` would turn into a 500, not the `{duplicate: true}` the docstring promises).
    """
    barrier = threading.Barrier(2)
    results: list[object] = [None, None]
    errors: list[BaseException | None] = [None, None]

    def _call(slot: int) -> None:
        barrier.wait(timeout=5)  # line both threads up at the same instant
        try:
            results[slot] = engine_mod.run_turn(_envelope(), session_factory=SessionLocal)
        except BaseException as exc:  # noqa: BLE001 - capture, do not let it kill the thread
            errors[slot] = exc

    threads = [threading.Thread(target=_call, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    # -- what actually happened, reported plainly rather than asserted blind ----------- #
    exceptions = [e for e in errors if e is not None]
    outcomes = [r for r in results if r is not None]

    cleanup_check = SessionLocal()
    try:
        rows = (
            cleanup_check.query(ChatbotTurn)
            .filter(
                ChatbotTurn.contact_respond_id == CONTACT_ID,
                ChatbotTurn.message_id == MESSAGE_ID,
            )
            .all()
        )
    finally:
        cleanup_check.close()

    assert len(rows) == 1, (
        f"exactly one turn row must exist after the race, found {len(rows)}: "
        f"{[(r.id, r.status) for r in rows]}"
    )

    if exceptions:
        # FINDING (do not fix here, per the tester brief): the SELECT-then-INSERT gap is
        # not closed by a caught IntegrityError, so the LOSING thread of a genuine race
        # raises instead of returning {duplicate: true}. Recorded, not asserted away.
        pytest.fail(
            "D15 defect reproduced: the losing thread raised instead of returning a "
            f"duplicate result - {type(exceptions[0]).__name__}: {exceptions[0]}\n"
            "engine.run_turn's SELECT-then-INSERT dedup (app/services/chatbot/engine.py, "
            "_existing_turn / _insert_turn) has no IntegrityError handling around the "
            "unique constraint (uq_chatbot_turns_contact_message) it relies on as the "
            "backstop. The app/api/v1/external/chat.py generic `except Exception` handler "
            "turns this into a 500 for the caller, not the {duplicate: true} the "
            "ChatbotTurn model docstring promises."
        )

    assert len(outcomes) == 2, "both threads must return a result when there is no defect"
    duplicate_flags = [bool(r.duplicate) for r in outcomes]
    assert duplicate_flags.count(True) == 1, (
        f"exactly one of the two results must be the duplicate, got {duplicate_flags}"
    )
    assert duplicate_flags.count(False) == 1


def test_forced_toctou_window_between_the_select_and_the_insert(
    real_db_contact, stub_engine_seams, monkeypatch
):
    """Deterministic version of the test above. Nine runs of the natural-timing test
    passed cleanly - `SessionLocal`'s connection-pool warm-up cost for the SECOND thread
    (a brand-new physical connection) reliably lets the first thread finish its whole
    SELECT-INSERT-COMMIT before the second thread's SELECT even runs, so the race never
    actually opens under plain thread-timing.

    This forces the window instead of hoping for it: a barrier INSIDE `_existing_turn`
    holds each thread until BOTH have completed their "does a turn already exist" SELECT
    (both see None), so both then genuinely race into `_insert_turn` regardless of
    connection warm-up or GIL scheduling luck - the same TOCTOU shape a slow webhook
    delivery racing a slow poller re-delivery could hit in production.
    """
    inner_barrier = threading.Barrier(2)
    original_existing_turn = engine_mod._existing_turn

    def _synced_existing_turn(db, **kw):
        result = original_existing_turn(db, **kw)
        inner_barrier.wait(timeout=5)  # both SELECTs must land before either INSERT
        return result

    monkeypatch.setattr(engine_mod, "_existing_turn", _synced_existing_turn)

    results: list[object] = [None, None]
    errors: list[BaseException | None] = [None, None]

    def _call(slot: int) -> None:
        try:
            results[slot] = engine_mod.run_turn(_envelope(), session_factory=SessionLocal)
        except BaseException as exc:  # noqa: BLE001 - capture, do not let it kill the thread
            errors[slot] = exc

    threads = [threading.Thread(target=_call, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    exceptions = [e for e in errors if e is not None]
    outcomes = [r for r in results if r is not None]

    cleanup_check = SessionLocal()
    try:
        rows = (
            cleanup_check.query(ChatbotTurn)
            .filter(
                ChatbotTurn.contact_respond_id == CONTACT_ID,
                ChatbotTurn.message_id == MESSAGE_ID,
            )
            .all()
        )
    finally:
        cleanup_check.close()

    assert len(rows) == 1, (
        f"exactly one turn row must exist after the forced race, found {len(rows)}: "
        f"{[(r.id, r.status) for r in rows]}"
    )

    if exceptions:
        pytest.fail(
            "D15 defect reproduced under a FORCED TOCTOU window: the losing thread raised "
            f"instead of returning a duplicate result - "
            f"{type(exceptions[0]).__name__}: {exceptions[0]}\n"
            "engine.run_turn's SELECT-then-INSERT dedup (app/services/chatbot/engine.py, "
            "_existing_turn / _insert_turn) has no IntegrityError handling around the "
            "unique constraint (uq_chatbot_turns_contact_message) it relies on as the "
            "backstop. app/api/v1/external/chat.py's generic `except Exception` handler "
            "turns this into a plain 500 for the caller, not the {duplicate: true} the "
            "ChatbotTurn model's own docstring promises for a genuine race."
        )

    assert len(outcomes) == 2, "both threads must return a result when there is no defect"
    duplicate_flags = [bool(r.duplicate) for r in outcomes]
    assert duplicate_flags.count(True) == 1, (
        f"exactly one of the two results must be the duplicate, got {duplicate_flags}"
    )
    assert duplicate_flags.count(False) == 1
