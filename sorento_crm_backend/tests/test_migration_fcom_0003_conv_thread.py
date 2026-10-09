"""fcom_0003 (parent_id, edited_at, deleted_at on entity_conversation_messages) run over a
schema rewound to its pre-migration shape. Same approach as the fcom_0002 test: rebuild the
old shape from the blank model schema, run the real migration file, assert the result.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests._pg_fixture import blank_session

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "fcom_0003_conv_thread.py"
COLUMNS = ("parent_id", "edited_at", "deleted_at")


def _run(db, direction: str = "upgrade"):
    assert MIGRATION.exists(), f"missing migration file {MIGRATION.name}"
    spec = importlib.util.spec_from_file_location("fcom_0003", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        getattr(module, direction)()


def _columns(db) -> dict[str, dict]:
    rows = db.execute(
        text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'entity_conversation_messages'"
        )
    ).mappings()
    return {r["column_name"]: dict(r) for r in rows}


def _self_fk(db) -> list[tuple[str, str]]:
    return [
        (r[0], r[1])
        for r in db.execute(
            text(
                "SELECT kcu.column_name, rc.delete_rule "
                "FROM information_schema.referential_constraints rc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON kcu.constraint_name = rc.constraint_name AND kcu.constraint_schema = rc.constraint_schema "
                "JOIN information_schema.table_constraints tc "
                "  ON tc.constraint_name = rc.constraint_name AND tc.constraint_schema = rc.constraint_schema "
                "JOIN information_schema.constraint_column_usage ccu "
                "  ON ccu.constraint_name = rc.constraint_name AND ccu.constraint_schema = rc.constraint_schema "
                "WHERE tc.table_schema = current_schema() AND tc.table_name = 'entity_conversation_messages' "
                "AND ccu.table_name = 'entity_conversation_messages' AND kcu.column_name = 'parent_id'"
            )
        )
    ]


@pytest.fixture
def db():
    """The blank schema with the three thread columns removed: the shape before fcom_0003."""
    with blank_session() as session:
        for col in COLUMNS:
            session.execute(text(f"ALTER TABLE entity_conversation_messages DROP COLUMN IF EXISTS {col} CASCADE"))
        assert not set(COLUMNS) & set(_columns(session))
        yield session


def test_upgrade_adds_the_three_nullable_columns(db):
    _run(db)
    cols = _columns(db)
    assert set(COLUMNS) <= set(cols)
    assert all(cols[c]["is_nullable"] == "YES" for c in COLUMNS)
    assert cols["edited_at"]["data_type"].startswith("timestamp")
    assert cols["deleted_at"]["data_type"].startswith("timestamp")


def test_upgrade_adds_a_self_foreign_key_that_cascades(db):
    _run(db)
    assert _self_fk(db) == [("parent_id", "CASCADE")]


def test_downgrade_removes_the_columns_and_the_foreign_key(db):
    _run(db)
    _run(db, "downgrade")
    assert not set(COLUMNS) & set(_columns(db))
    assert _self_fk(db) == []


def test_the_model_declares_the_thread_columns():
    from app.models.entity_conversation import EntityConversationMessage

    assert set(COLUMNS) <= set(EntityConversationMessage.__table__.columns.keys())
