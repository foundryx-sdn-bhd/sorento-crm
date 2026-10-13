"""Migration `dps_0001_skip_period_questions` (DO-PERIOD-SKIP AC-1): up, down, up on scratch tables.

`respond_contacts.chatbot_skip_period_questions` BOOLEAN NOT NULL DEFAULT false; every existing
contact reads false. Additive and re-runnable (ADD COLUMN IF NOT EXISTS). Its SQL names the table
unqualified, so `search_path` points it at a scratch schema's copy, never the shared relation.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database import engine

MIGRATION = (
    Path(__file__).resolve().parent / ".." / "alembic" / "versions" / "dps_0001_skip_period_questions.py"
).resolve()
COLUMN = "chatbot_skip_period_questions"


def _load():
    spec = importlib.util.spec_from_file_location("m_dps_0001", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _column(conn, schema):
    return conn.execute(
        sa.text(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = 'respond_contacts' AND column_name = :c"
        ),
        {"s": schema, "c": COLUMN},
    ).first()


def test_revision_id_is_short_and_named():
    module = _load()
    assert module.revision == "dps_0001_skip_period_questions"
    assert len(module.revision) <= 32
    assert module.down_revision


def _scratch(conn):
    schema = f"zzt_dps_{uuid.uuid4().hex[:8]}"
    conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
    conn.commit()
    conn.execute(sa.text(f"SET search_path TO {schema}"))
    conn.execute(sa.text("CREATE TABLE respond_contacts (id text PRIMARY KEY)"))
    return schema


def _drop(conn, schema):
    conn.execute(sa.text("SET search_path TO DEFAULT"))
    conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    conn.commit()


def test_existing_contacts_read_false_and_new_ones_default_false_and_it_is_reversible():
    module = _load()
    with engine.connect() as conn:
        schema = _scratch(conn)
        try:
            conn.execute(sa.text("INSERT INTO respond_contacts (id) VALUES ('a'), ('b')"))
            _run(conn, module.upgrade)
            col = _column(conn, schema)
            assert col is not None and col.is_nullable == "NO" and "false" in (col.column_default or "")
            assert conn.execute(
                sa.text(f"SELECT count(*) FROM respond_contacts WHERE {COLUMN} IS NOT FALSE")
            ).scalar() == 0
            conn.execute(sa.text("INSERT INTO respond_contacts (id) VALUES ('new')"))
            assert conn.execute(
                sa.text(f"SELECT {COLUMN} FROM respond_contacts WHERE id = 'new'")
            ).scalar() is False
            _run(conn, module.upgrade)  # re-runnable against a create_all schema
            _run(conn, module.downgrade)
            assert _column(conn, schema) is None
            _run(conn, module.upgrade)
            assert _column(conn, schema) is not None
            conn.rollback()
        finally:
            _drop(conn, schema)
