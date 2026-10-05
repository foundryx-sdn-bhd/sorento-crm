"""Migration `aig_0001_chatbot_allowed` (AI-ALLOWED-GATE): up, down, up on scratch tables.

`respond_contacts.chatbot_allowed` BOOLEAN NOT NULL DEFAULT false (owner ruling 5 Oct 2026, Q1 a).
Every contact that exists at migration time is backfilled TRUE (production behaviour does not
change on release, AC-AI-1); a row inserted afterwards reads FALSE by the column default, so a
new contact is blocked until a human ticks it. Re-runnable (ADD COLUMN IF NOT EXISTS)
and the downgrade drops the column (AC-AI-2). Its SQL names the table unqualified, so
`search_path` points it at a scratch schema's copy, never the shared relation. Named
`test_migration_*.py` so CI runs it in the serial migration pass.
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
    Path(__file__).resolve().parent / ".." / "alembic" / "versions" / "aig_0001_chatbot_allowed.py"
).resolve()


def _load():
    assert MIGRATION.exists(), f"{MIGRATION} is missing"
    spec = importlib.util.spec_from_file_location("m_aig_0001", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _column(conn, schema, table):
    return conn.execute(
        sa.text(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t AND column_name = 'chatbot_allowed'"
        ),
        {"s": schema, "t": table},
    ).first()


def _scratch(conn):
    schema = f"zzt_aig_{uuid.uuid4().hex[:8]}"
    conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
    conn.commit()
    conn.execute(sa.text(f"SET search_path TO {schema}"))
    conn.execute(sa.text("CREATE TABLE respond_contacts (id text PRIMARY KEY)"))
    return schema


def _drop(conn, schema):
    conn.execute(sa.text("SET search_path TO DEFAULT"))
    conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    conn.commit()


def test_revision_chains_onto_mains_head():
    module = _load()
    assert module.revision == "aig_0001_chatbot_allowed"
    assert len(module.revision) <= 32
    assert module.down_revision == "dps_0001_skip_period_questions"


def test_existing_contacts_read_true_after_upgrade_and_a_new_row_defaults_false():
    module = _load()
    with engine.connect() as conn:
        schema = _scratch(conn)
        try:
            conn.execute(sa.text("INSERT INTO respond_contacts (id) VALUES ('dealer'), ('office'), ('plain')"))

            _run(conn, module.upgrade)
            col = _column(conn, schema, "respond_contacts")
            assert col is not None and col.is_nullable == "NO" and "false" in (col.column_default or "")
            assert conn.execute(
                sa.text("SELECT count(*) FROM respond_contacts WHERE chatbot_allowed IS NOT TRUE")
            ).scalar() == 0
            conn.execute(sa.text("INSERT INTO respond_contacts (id) VALUES ('new')"))
            assert conn.execute(
                sa.text("SELECT chatbot_allowed FROM respond_contacts WHERE id = 'new'")
            ).scalar() is False
            conn.rollback()
        finally:
            _drop(conn, schema)


def test_upgrade_is_rerunnable_without_flipping_a_blocked_row_back():
    module = _load()
    with engine.connect() as conn:
        schema = _scratch(conn)
        try:
            conn.execute(sa.text("INSERT INTO respond_contacts (id) VALUES ('a')"))
            _run(conn, module.upgrade)
            conn.execute(sa.text("UPDATE respond_contacts SET chatbot_allowed = false WHERE id = 'a'"))

            _run(conn, module.upgrade)
            assert conn.execute(
                sa.text("SELECT chatbot_allowed FROM respond_contacts WHERE id = 'a'")
            ).scalar() is False
            conn.rollback()
        finally:
            _drop(conn, schema)


def test_downgrade_drops_the_column_and_upgrade_restores_it():
    module = _load()
    with engine.connect() as conn:
        schema = _scratch(conn)
        try:
            conn.execute(sa.text("INSERT INTO respond_contacts (id) VALUES ('a')"))
            _run(conn, module.upgrade)
            _run(conn, module.downgrade)
            assert _column(conn, schema, "respond_contacts") is None
            _run(conn, module.upgrade)
            assert _column(conn, schema, "respond_contacts") is not None
            assert conn.execute(
                sa.text("SELECT chatbot_allowed FROM respond_contacts WHERE id = 'a'")
            ).scalar() is True
            conn.rollback()
        finally:
            _drop(conn, schema)
