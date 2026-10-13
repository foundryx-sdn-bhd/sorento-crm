"""Migration `chat_backfill_0001`: facts that name a chat history row, and the run table
(chat analytics CA-4, tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca4-backfill-09oct.md, rule B8; UAC AC-1
to AC-3. `chat_ask_facts.turn_id` becomes nullable, `history_id bigint` is added,
exactly one of the two is set, one fact per (`history_id`, `ask_seq`), and
`chatbot.chat_backfill_runs` records a run with no column for anyone's words.

Run: pytest tests/chatbot/test_migration_chat_backfill_0001.py -q
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests import _pg_fixture
from tests._pg_fixture import blank_session

STEM = "chat_backfill_0001"
PARENT = "rts_0001_runtime_settings"
_MIGRATION = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions" / f"{STEM}.py"


def _module():
    assert _MIGRATION.exists(), f"{_MIGRATION} does not exist"
    spec = importlib.util.spec_from_file_location("zzt_migration_chat_backfill_0001", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema() -> str:
    return f"{_pg_fixture._BLANK['name']}_chatbot"


def _run(db, *, twice: bool = False) -> None:
    context = MigrationContext.configure(connection=db.connection())
    with Operations.context(context):
        _module().upgrade()
        if twice:
            _module().upgrade()


def test_the_revision_fits_the_version_column_and_chains_onto_main_head():
    module = _module()
    assert module.revision == STEM
    assert len(module.revision) <= 32
    assert module.down_revision == PARENT


@pytest.fixture
def db():
    """The blank schema as it stood BEFORE this migration: no run table, no history column,
    `turn_id` NOT NULL."""
    with blank_session() as session:
        s = _schema()
        session.execute(text(f'DROP TABLE IF EXISTS "{s}".chat_backfill_runs CASCADE'))
        session.execute(text(f'DELETE FROM "{s}".chat_ask_facts'))
        session.execute(
            text(f'ALTER TABLE "{s}".chat_ask_facts DROP CONSTRAINT IF EXISTS ck_chat_ask_facts_one_source')
        )
        session.execute(
            text(f'ALTER TABLE "{s}".chat_ask_facts DROP CONSTRAINT IF EXISTS uq_chat_ask_facts_history_seq')
        )
        session.execute(text(f'ALTER TABLE "{s}".chat_ask_facts DROP COLUMN IF EXISTS history_id'))
        session.execute(text(f'ALTER TABLE "{s}".chat_ask_facts ALTER COLUMN turn_id SET NOT NULL'))
        yield session


def _columns(db, table: str) -> dict:
    return {
        r.column_name: r
        for r in db.execute(
            text(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t"
            ),
            {"s": _schema(), "t": table},
        )
    }


def _fact(db, *, turn_id=None, history_id=None, seq: int = 1) -> None:
    db.execute(
        text(
            f'INSERT INTO "{_schema()}".chat_ask_facts '
            "(id, source, turn_id, history_id, ask_seq, contact_respond_id, audience, "
            "contact_type, asked_at, outcome, tools) "
            "VALUES (:id, 'history_backfill', :turn, :hid, :seq, 'ZZT-mig', 'unknown', 'unknown', "
            "now(), 'answered', '{}')"
        ),
        {"id": str(uuid.uuid4()), "turn": turn_id, "hid": history_id, "seq": seq},
    )


def test_a_fact_can_name_a_history_row_instead_of_a_turn(db):
    _run(db)
    columns = _columns(db, "chat_ask_facts")
    assert columns["turn_id"].is_nullable == "YES"
    assert columns["history_id"].data_type == "bigint"
    assert columns["history_id"].is_nullable == "YES"
    _fact(db, history_id=101)


def test_a_fact_names_exactly_one_source(db):
    _run(db)
    with pytest.raises(Exception):
        with db.begin_nested():
            _fact(db)  # neither
    with pytest.raises(Exception):
        with db.begin_nested():
            _fact(db, turn_id=str(uuid.uuid4()), history_id=102)  # both


def test_one_fact_per_history_row_and_ask(db):
    _run(db)
    _fact(db, history_id=103, seq=1)
    _fact(db, history_id=103, seq=2)
    with pytest.raises(Exception):
        with db.begin_nested():
            _fact(db, history_id=103, seq=1)


def test_upgrade_creates_the_run_table_with_the_contract_columns(db):
    _run(db)
    columns = _columns(db, "chat_backfill_runs")
    assert set(columns) == {
        "id", "era", "status", "messages_eligible", "messages_read", "messages_skipped",
        "asks_written", "calls", "failed_calls", "tokens_in", "tokens_out", "tokens_cached",
        "model", "prompt_version", "taxonomy_version", "row_limit", "last_history_id",
        "started_at", "finished_at", "error",
    }
    for required in ("id", "era", "status", "started_at"):
        assert columns[required].is_nullable == "NO", required


def test_no_run_column_can_hold_what_a_customer_wrote(db):
    _run(db)
    for name in _columns(db, "chat_backfill_runs"):
        for fragment in ("message_text", "text", "words", "body", "summary", "phone", "reply"):
            assert fragment not in name, name


def test_upgrade_twice_is_harmless(db):
    _run(db, twice=True)
    assert "history_id" in _columns(db, "chat_ask_facts")
