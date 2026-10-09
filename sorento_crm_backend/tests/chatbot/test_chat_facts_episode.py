"""CA-1 facts: the episode snapshot at frame close (tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca1-facts-09oct.md, "Hook 2":
`memory.write_episode_for_reset` calls `facts.snapshot_episode` after the frame insert and
BEFORE `_trim_to_newest`, in the same transaction, inside a SAVEPOINT so an analytics failure
never loses the frame.

`app.services.chatbot.analytics.facts` and `app.models.chat_facts` do not exist yet, so each
test fails with `ModuleNotFoundError`. Frame plumbing (the reset range, the trim to 20) is
real: it goes through `memory.write_episode_for_reset` over seeded `chatbot.turns` rows.

The schema test is the privacy contract in executable form: none of the three tables may
have a column that could hold customer words, an episode summary or a contact's identity.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.conversation_frame import ConversationFrame
from app.services.chatbot.turn import memory as memory_mod
from tests import _pg_fixture
from tests.chatbot._chat_facts_helpers import (
    answered_trace,
    apply_event,
    episode_rows,
    fact_rows,
    facts_mod,
    looked_up,
    models_mod,
    register_company_scope,
    seed_contact,
    seed_turn,
)

UTC = timezone.utc
BASE = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope()


def _cid(tag: str) -> str:
    return f"ZZT-ep-{tag}-{uuid.uuid4().hex[:8]}"


def _minute(n: int) -> datetime:
    return BASE + timedelta(minutes=n)


def _inventory_answer() -> list[dict]:
    return answered_trace(domain="inventory", intent="stock")


def _record_all(session_factory, turn_ids: list[str]) -> None:
    for turn_id in turn_ids:
        assert facts_mod().record_turn(session_factory(), turn_id) is True


def _close_episode(session_factory, cid: str, resetting_turn_id: str):
    return memory_mod.write_episode_for_reset(
        session_factory(), contact_respond_id=cid, is_test=False, resetting_turn_id=resetting_turn_id
    )


def _five_turn_episode(session_factory, cid: str) -> tuple[list[str], str]:
    """answered, noise, failed, missed, handover; then the resetting turn."""
    t1 = seed_turn(session_factory, cid, created_at=_minute(0), records=_inventory_answer())
    t2 = seed_turn(session_factory, cid, created_at=_minute(1), records=[], branch_kind="low_signal")
    t3 = seed_turn(
        session_factory, cid, created_at=_minute(2), records=_inventory_answer(), status="failed", stage_name="replied"
    )
    t4 = seed_turn(
        session_factory, cid, created_at=_minute(3),
        records=[apply_event(domain_hint="inventory", domains=["inventory"]), looked_up(missed=["inventory"])],
    )
    t5 = seed_turn(
        session_factory, cid, created_at=_minute(4),
        records=[apply_event(domain_hint="inventory", domains=["inventory"], lane="escalation")],
        branch_kind="out_of_scope",
    )
    reset = seed_turn(
        session_factory, cid, created_at=_minute(10),
        records=[apply_event(domain_hint="order", domains=["order"], topic_reset=True)],
    )
    return [t1, t2, t3, t4, t5], reset


# --------------------------------------------------------------------------- the snapshot


def test_closing_an_episode_writes_its_analytic_snapshot(session_factory):
    cid = _cid("snap")
    seed_contact(session_factory, cid, customer=True)
    turn_ids, reset = _five_turn_episode(session_factory, cid)
    _record_all(session_factory, turn_ids + [reset])

    frame = _close_episode(session_factory, cid, reset)

    assert frame is not None
    rows = episode_rows(session_factory, frame_id=frame.id)
    assert len(rows) == 1
    row = rows[0]
    assert str(row.frame_id) == frame.id
    assert row.contact_respond_id == cid
    assert row.turn_count == 5
    assert row.ask_count == 4, "the low_signal turn is noise, not an ask"
    assert row.failed_turns == 2, "technical and missed fail; the handover and the noise do not"
    assert row.last_outcome == "handover"
    assert row.handover is True
    assert row.close_reason == frame.close_reason == "topic_switch"
    assert row.domain == frame.domain == "inventory"
    assert row.intent == frame.intent
    assert row.opened_at is not None and row.closed_at is not None
    assert row.opened_at <= row.closed_at
    # CA-2 fills these.
    assert row.resolved is None and row.asked_again is None


def test_the_snapshot_stamps_episode_id_on_the_closed_turns_only(session_factory):
    cid = _cid("stamp")
    seed_contact(session_factory, cid)
    turn_ids, reset = _five_turn_episode(session_factory, cid)
    _record_all(session_factory, turn_ids + [reset])

    frame = _close_episode(session_factory, cid, reset)

    for turn_id in turn_ids:
        assert str(fact_rows(session_factory, turn_id=turn_id)[0].episode_id) == frame.id, turn_id
    assert fact_rows(session_factory, turn_id=reset)[0].episode_id is None, "the resetting turn opens the NEXT episode"


def test_the_snapshot_counts_turns_even_when_a_turn_has_no_fact_row_yet(session_factory):
    cid = _cid("gap")
    seed_contact(session_factory, cid)
    t1 = seed_turn(session_factory, cid, created_at=_minute(0), records=_inventory_answer())
    t2 = seed_turn(session_factory, cid, created_at=_minute(1), records=_inventory_answer())
    reset = seed_turn(session_factory, cid, created_at=_minute(5), records=[apply_event(topic_reset=True)])
    _record_all(session_factory, [t1])  # t2's recorder "failed"; the sweep has not run

    frame = _close_episode(session_factory, cid, reset)

    row = episode_rows(session_factory, frame_id=frame.id)[0]
    assert (row.turn_count, row.ask_count, row.failed_turns) == (2, 2, 0)
    assert str(fact_rows(session_factory, turn_id=t1)[0].episode_id) == frame.id
    assert fact_rows(session_factory, turn_id=t2) == []


def test_closing_the_same_range_twice_leaves_one_snapshot(session_factory):
    cid = _cid("twice")
    seed_contact(session_factory, cid)
    turn_ids, reset = _five_turn_episode(session_factory, cid)
    _record_all(session_factory, turn_ids)

    first = _close_episode(session_factory, cid, reset)
    second = _close_episode(session_factory, cid, reset)

    assert first is not None and second is None
    assert len(episode_rows(session_factory, contact_respond_id=cid)) == 1


def test_snapshot_episode_does_not_commit(session_factory):
    """It joins the caller's transaction: rolling the caller back takes the snapshot too."""
    cid = _cid("nocommit")
    seed_contact(session_factory, cid)
    turn_ids, _reset = _five_turn_episode(session_factory, cid)
    _record_all(session_factory, turn_ids)

    writer = session_factory()
    frame_id = str(uuid.uuid4())
    writer.add(
        ConversationFrame(
            id=frame_id, contact_id=cid, contact_respond_id=cid, space_id="0", channel="whatsapp",
            domain="inventory", status="closed", close_reason="topic_switch", turn_ids=turn_ids,
            started_at=_minute(0).replace(tzinfo=None), opened_at=_minute(0).replace(tzinfo=None),
            last_activity_at=_minute(4).replace(tzinfo=None), closed_at=_minute(5).replace(tzinfo=None),
            is_test=False,
        )
    )
    writer.commit()

    from app.models.chatbot_turn import ChatbotTurn

    rows = (
        session_factory().query(ChatbotTurn).filter(ChatbotTurn.id.in_(turn_ids)).order_by(ChatbotTurn.created_at).all()
    )
    snapshotter = session_factory()
    facts_mod().snapshot_episode(snapshotter, frame_id, rows)
    snapshotter.rollback()

    assert episode_rows(session_factory, frame_id=frame_id) == []
    assert all(fact_rows(session_factory, turn_id=t)[0].episode_id is None for t in turn_ids)


# --------------------------------------------------------------------------- trimming and failure


def test_a_trimmed_frame_keeps_its_snapshot_and_its_turns_keep_their_episode_id(session_factory):
    cid = _cid("trim")
    seed_contact(session_factory, cid)
    keep = memory_mod.KEEP_EPISODES
    turn_ids = [
        seed_turn(session_factory, cid, created_at=_minute(i * 2), records=_inventory_answer())
        for i in range(keep + 2)
    ]
    _record_all(session_factory, turn_ids)

    frames = []
    for i in range(1, keep + 2):  # keep + 1 closes: frame k holds turn k-1
        frames.append(_close_episode(session_factory, cid, turn_ids[i]))
    assert all(f is not None for f in frames)

    surviving = {
        f.id for f in session_factory().query(ConversationFrame).filter(ConversationFrame.contact_respond_id == cid)
    }
    assert len(surviving) == keep
    trimmed = frames[0]
    assert trimmed.id not in surviving, "the 21st close trimmed the oldest frame"

    snapshots = {str(r.frame_id) for r in episode_rows(session_factory, contact_respond_id=cid)}
    assert trimmed.id in snapshots, "the trimmed frame's analytic row remains"
    assert len(snapshots) == keep + 1
    assert str(fact_rows(session_factory, turn_id=turn_ids[0])[0].episode_id) == trimmed.id, (
        "episode_id has no foreign key to the frame: it outlives the trim"
    )


def test_an_analytics_failure_never_loses_the_frame(session_factory, monkeypatch):
    """The failure is a DB error, which aborts a Postgres transaction unless the snapshot ran
    inside a SAVEPOINT. The frame must still be committed."""
    cid = _cid("boom")
    seed_contact(session_factory, cid)
    turn_ids, reset = _five_turn_episode(session_factory, cid)
    _record_all(session_factory, turn_ids)

    def db_boom(db, frame_id, turn_rows):
        db.execute(text("SELECT 1/0"))

    monkeypatch.setattr(facts_mod(), "snapshot_episode", db_boom)

    frame = _close_episode(session_factory, cid, reset)

    assert frame is not None
    stored = session_factory().query(ConversationFrame).filter(ConversationFrame.id == frame.id).first()
    assert stored is not None and list(stored.turn_ids) == turn_ids
    assert episode_rows(session_factory, contact_respond_id=cid) == []


def test_a_plain_exception_in_the_snapshot_also_keeps_the_frame(session_factory, monkeypatch):
    cid = _cid("raise")
    seed_contact(session_factory, cid)
    turn_ids, reset = _five_turn_episode(session_factory, cid)

    def boom(db, frame_id, turn_rows):
        raise RuntimeError("ZZT analytics down")

    monkeypatch.setattr(facts_mod(), "snapshot_episode", boom)

    frame = _close_episode(session_factory, cid, reset)

    assert frame is not None
    assert session_factory().query(ConversationFrame).filter(ConversationFrame.id == frame.id).count() == 1


def test_a_console_test_world_frame_writes_no_snapshot(session_factory):
    """Test turns never become facts, so a console dry-run episode has nothing to snapshot."""
    cid = _cid("console")
    t1 = seed_turn(session_factory, cid, created_at=_minute(0), is_test=True, ingress="console")
    reset = seed_turn(session_factory, cid, created_at=_minute(5), is_test=True, ingress="console")

    frame = memory_mod.write_episode_for_reset(
        session_factory(), contact_respond_id=cid, is_test=True, resetting_turn_id=reset
    )

    assert frame is not None and t1
    assert episode_rows(session_factory, contact_respond_id=cid) == []


# --------------------------------------------------------------------------- fix round 1


def test_a_superseded_retry_attempt_is_not_counted_in_the_episode(session_factory):
    """Only the latest attempt of a respond message counts: a failed first try that was
    retried successfully is one answered turn, not a failure plus an answer."""
    cid = _cid("retry")
    seed_contact(session_factory, cid)
    first = seed_turn(
        session_factory, cid, created_at=_minute(0), message_id="ZZT-ep-retry", attempt=1,
        records=_inventory_answer(), status="failed", stage_name="replied",
    )
    second = seed_turn(
        session_factory, cid, created_at=_minute(1), message_id="ZZT-ep-retry", attempt=2, records=_inventory_answer()
    )
    other = seed_turn(session_factory, cid, created_at=_minute(2), records=_inventory_answer())
    reset = seed_turn(session_factory, cid, created_at=_minute(10), records=[apply_event(topic_reset=True)])
    _record_all(session_factory, [second, other])
    assert first and reset

    frame = _close_episode(session_factory, cid, reset)

    row = episode_rows(session_factory, frame_id=frame.id)[0]
    assert (row.turn_count, row.ask_count, row.failed_turns, row.last_outcome) == (2, 2, 0, "answered")


def test_the_snapshot_exists_even_when_the_inserted_frame_is_itself_trimmed(session_factory):
    """A backfill-shaped close: the contact already holds 20 frames that are all newer, so
    the retention trim drops the frame just written. The snapshot is taken first."""
    cid = _cid("selftrim")
    seed_contact(session_factory, cid)
    writer = session_factory()
    for i in range(memory_mod.KEEP_EPISODES):
        writer.add(
            ConversationFrame(
                id=str(uuid.uuid4()), contact_id=cid, contact_respond_id=cid, space_id="0", channel="whatsapp",
                domain="inventory", status="closed", close_reason="topic_switch", turn_ids=[f"ZZT-synthetic-{cid}-{i}"],
                started_at=datetime(2026, 9, 1), opened_at=datetime(2026, 9, 1),
                last_activity_at=datetime(2026, 9, 30, 0, i), closed_at=datetime(2026, 9, 30, 1, i), is_test=False,
            )
        )
    writer.commit()
    t1 = seed_turn(session_factory, cid, created_at=_minute(0), records=_inventory_answer())
    reset = seed_turn(session_factory, cid, created_at=_minute(5), records=[apply_event(topic_reset=True)])
    _record_all(session_factory, [t1])

    frame = _close_episode(session_factory, cid, reset)

    assert frame is None, "the trim removed the new frame, so the writer reads nothing back"
    rows = episode_rows(session_factory, contact_respond_id=cid)
    assert len(rows) == 1 and rows[0].turn_count == 1
    assert str(fact_rows(session_factory, turn_id=t1)[0].episode_id) == str(rows[0].frame_id)


# --------------------------------------------------------------------------- schema privacy


_BANNED = re.compile(r"message|text|summary|last_user|phone|name|body|content", re.I)


def _columns(session_factory, table: str) -> list[str]:
    schema = f"{_pg_fixture._BLANK['name']}_chatbot"
    rows = session_factory().execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position"
        ),
        {"s": schema, "t": table},
    )
    return [r[0] for r in rows]


@pytest.mark.parametrize("table", ["chat_ask_facts", "chat_ask_items", "chat_episode_facts"])
def test_no_column_can_hold_customer_words_a_summary_or_an_identity(session_factory, table):
    models_mod()  # the tables exist only once the model is registered
    columns = _columns(session_factory, table)
    assert columns, f"chatbot.{table} is missing from the schema"
    offenders = [c for c in columns if _BANNED.search(c)]
    assert offenders == [], f"{table} has free-text-shaped columns: {offenders}"


def test_the_three_tables_have_the_contract_columns(session_factory):
    models_mod()
    asks = set(_columns(session_factory, "chat_ask_facts"))
    assert {
        "id", "source", "turn_id", "ask_seq", "contact_respond_id", "contact_id", "customer_id",
        "company_id", "audience", "asked_at", "channel", "domain", "intent", "outcome",
        "failure_reason", "branch_kind", "tools", "rows_offered", "reply_ms", "is_media",
        "phantom_dropped", "episode_id", "topic_code", "taxonomy_version", "label_model",
        "label_prompt_version", "labelled_at", "label_confidence", "created_at",
    } <= asks
    assert {"id", "ask_id", "kind", "ref_id", "role", "resolved_by"} <= set(
        _columns(session_factory, "chat_ask_items")
    )
    assert {
        "frame_id", "contact_respond_id", "opened_at", "closed_at", "domain", "intent", "turn_count",
        "ask_count", "failed_turns", "last_outcome", "handover", "close_reason", "resolved", "asked_again",
    } <= set(_columns(session_factory, "chat_episode_facts"))
