"""Migration `esc1_0001_escalation_allowed` (ESCALATION-CONTROL): up, down, up on scratch tables.

Owner change and ruling, 30 Sep 2026: one per-contact flag, `respond_contacts.
escalation_allowed` BOOLEAN NOT NULL DEFAULT true, and EVERY existing contact is backfilled
allowed (no dealer-blocked backfill; blocking is only by unticking the contact page). Access
types get no column. The migration also converges a copy that ran this lane's earlier SQL
(the column nullable, some rows NULL). Its SQL names the table unqualified, so `search_path`
points it at a scratch schema's copy, never the shared relation. Named `test_migration_*.py`
so CI runs it in the serial migration pass.
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
    Path(__file__).resolve().parent / ".." / "alembic" / "versions" / "esc1_0001_escalation_allowed.py"
).resolve()


def _load():
    spec = importlib.util.spec_from_file_location("m_esc1_0001", MIGRATION)
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
            "WHERE table_schema = :s AND table_name = :t AND column_name = 'escalation_allowed'"
        ),
        {"s": schema, "t": table},
    ).first()


def test_revision_chains_onto_mains_head():
    module = _load()
    assert module.revision == "esc1_0001_escalation_allowed"
    assert len(module.revision) <= 32
    assert module.down_revision == "oihr_0004_wide_line_table"


def _scratch(conn):
    schema = f"zzt_esc1_{uuid.uuid4().hex[:8]}"
    conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
    conn.commit()
    conn.execute(sa.text(f"SET search_path TO {schema}"))
    conn.execute(sa.text("CREATE TABLE contact_access_types (code varchar(50) PRIMARY KEY, name varchar(255) NOT NULL)"))
    conn.execute(sa.text("CREATE TABLE respond_contacts (id text PRIMARY KEY)"))
    conn.execute(sa.text("INSERT INTO contact_access_types VALUES ('sd', 'Sorento Dealer'), ('eu', 'End User')"))
    return schema


def _drop(conn, schema):
    conn.execute(sa.text("SET search_path TO DEFAULT"))
    conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    conn.commit()


def test_every_existing_contact_is_backfilled_allowed_and_access_types_get_nothing():
    module = _load()
    with engine.connect() as conn:
        schema = _scratch(conn)
        try:
            conn.execute(sa.text("INSERT INTO respond_contacts (id) VALUES ('dealer'), ('office'), ('plain')"))

            _run(conn, module.upgrade)
            col = _column(conn, schema, "respond_contacts")
            assert col is not None and col.is_nullable == "NO" and "true" in (col.column_default or "")
            assert _column(conn, schema, "contact_access_types") is None
            assert conn.execute(
                sa.text("SELECT count(*) FROM respond_contacts WHERE escalation_allowed IS NOT TRUE")
            ).scalar() == 0
            conn.execute(sa.text("INSERT INTO respond_contacts (id) VALUES ('new')"))
            assert conn.execute(
                sa.text("SELECT escalation_allowed FROM respond_contacts WHERE id = 'new'")
            ).scalar() is True

            _run(conn, module.upgrade)  # re-runnable against a create_all schema
            _run(conn, module.downgrade)
            assert _column(conn, schema, "respond_contacts") is None
            _run(conn, module.upgrade)
            assert _column(conn, schema, "respond_contacts") is not None
            conn.rollback()
        finally:
            _drop(conn, schema)


def test_a_copy_that_ran_the_earlier_nullable_sql_converges():
    """The crew copy ran this lane's first SQL: a NULLable override column. The migration
    makes it NOT NULL DEFAULT true and reads every NULL as allowed; a contact already set
    to false stays false."""
    module = _load()
    with engine.connect() as conn:
        schema = _scratch(conn)
        try:
            conn.execute(sa.text("ALTER TABLE respond_contacts ADD COLUMN escalation_allowed BOOLEAN NULL"))
            conn.execute(
                sa.text("INSERT INTO respond_contacts VALUES ('inherit', NULL), ('blocked', false), ('ok', true)")
            )
            _run(conn, module.upgrade)
            rows = dict(conn.execute(sa.text("SELECT id, escalation_allowed FROM respond_contacts")).all())
            assert rows == {"inherit": True, "blocked": False, "ok": True}
            assert _column(conn, schema, "respond_contacts").is_nullable == "NO"
            conn.rollback()
        finally:
            _drop(conn, schema)
