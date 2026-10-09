"""Migration `chat_topics_0002_audit`: the topic audit table (chat analytics CA-2, tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md, build contract,
"Topic audit": `chatbot.chat_label_audits` (id, ask_id FK chat_ask_facts ON DELETE CASCADE,
taxonomy_version, drawn_at, verdict, corrected_topic FK chat_topics.code, products_verdict,
reviewed_by, reviewed_at), unique (ask_id, taxonomy_version), and NO column for the message.

Run: pytest tests/chatbot/test_migration_chat_topics_0002_audit.py -q
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

STEM = "chat_topics_0002_audit"
PARENT = "chat_topics_0001_taxonomy"
_MIGRATION = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions" / f"{STEM}.py"


def _module():
    assert _MIGRATION.exists(), f"{_MIGRATION} does not exist"
    spec = importlib.util.spec_from_file_location("zzt_migration_chat_topics_0002", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(db) -> None:
    context = MigrationContext.configure(connection=db.connection())
    with Operations.context(context):
        _module().upgrade()


def _schema() -> str:
    return f"{_pg_fixture._BLANK['name']}_chatbot"


def test_the_revision_fits_the_version_column_and_chains_onto_the_taxonomy():
    module = _module()
    assert module.revision == STEM
    assert len(module.revision) <= 32
    assert module.down_revision == PARENT


@pytest.fixture
def db():
    with blank_session() as session:
        session.execute(text(f'DROP TABLE IF EXISTS "{_schema()}".chat_label_audits CASCADE'))
        yield session


def _columns(db) -> dict:
    return {
        r.column_name: r
        for r in db.execute(
            text(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = 'chat_label_audits'"
            ),
            {"s": _schema()},
        )
    }


def test_upgrade_creates_the_audit_table_with_the_contract_columns(db):
    _run(db)
    columns = _columns(db)
    assert set(columns) == {
        "id", "ask_id", "taxonomy_version", "drawn_at", "verdict", "corrected_topic",
        "products_verdict", "reviewed_by", "reviewed_at",
    }
    for required in ("id", "ask_id", "taxonomy_version", "drawn_at"):
        assert columns[required].is_nullable == "NO", required
    for optional in ("verdict", "corrected_topic", "products_verdict", "reviewed_by", "reviewed_at"):
        assert columns[optional].is_nullable == "YES", optional
    assert columns["taxonomy_version"].data_type == "smallint"


def test_no_column_can_hold_what_a_customer_wrote(db):
    _run(db)
    for name in _columns(db):
        for fragment in ("message", "text", "words", "body", "summary", "phone"):
            assert fragment not in name, name


def _seed_fact(db) -> str:
    fact_id = str(uuid.uuid4())
    db.execute(
        text(
            f'INSERT INTO "{_schema()}".chat_ask_facts '
            "(id, source, turn_id, ask_seq, contact_respond_id, audience, contact_type, asked_at, outcome, tools) "
            "VALUES (:id, 'trace', :turn, 1, :c, 'dealer', 'dealer', now(), 'answered', '{}')"
        ),
        {"id": fact_id, "turn": str(uuid.uuid4()), "c": f"ZZT-{fact_id[:8]}"},
    )
    return fact_id


def _audit(db, fact_id: str, version: int = 2) -> None:
    db.execute(
        text(
            f'INSERT INTO "{_schema()}".chat_label_audits (id, ask_id, taxonomy_version) '
            "VALUES (:id, :ask, :v)"
        ),
        {"id": str(uuid.uuid4()), "ask": fact_id, "v": version},
    )


def test_an_ask_is_drawn_once_per_taxonomy_version(db):
    _run(db)
    fact_id = _seed_fact(db)
    _audit(db, fact_id, 2)
    _audit(db, fact_id, 3)  # another version is fine
    with pytest.raises(Exception):
        with db.begin_nested():
            _audit(db, fact_id, 2)


def test_deleting_the_ask_deletes_its_audit_row(db):
    _run(db)
    fact_id = _seed_fact(db)
    _audit(db, fact_id)
    db.execute(text(f'DELETE FROM "{_schema()}".chat_ask_facts WHERE id = :id'), {"id": fact_id})
    assert db.execute(text(f'SELECT count(*) FROM "{_schema()}".chat_label_audits')).scalar() == 0


def test_an_audit_row_needs_a_real_ask(db):
    _run(db)
    with pytest.raises(Exception):
        with db.begin_nested():
            _audit(db, str(uuid.uuid4()))


def test_a_corrected_topic_must_be_a_taxonomy_code(db):
    _run(db)
    fact_id = _seed_fact(db)
    with pytest.raises(Exception):
        with db.begin_nested():
            db.execute(
                text(
                    f'INSERT INTO "{_schema()}".chat_label_audits (id, ask_id, taxonomy_version, corrected_topic) '
                    "VALUES (:id, :ask, 2, 'zzt.nowhere')"
                ),
                {"id": str(uuid.uuid4()), "ask": fact_id},
            )
