"""Migration `do_hist_0001_tracking_assumed` (DO-HISTORY slice 1): up, down, up on scratch tables.

`orders.tracking_assumed_at` TIMESTAMPTZ NULL: set when the DO history backfill assumes a
pre-cutover DO delivered because it has no Order Tracking (owner Q2 a, 13 Oct 2026). Additive,
nullable, nothing backfilled. Re-runnable (ADD COLUMN IF NOT EXISTS) and the downgrade drops the
column. Its SQL names the table unqualified, so `search_path` points it at a scratch schema's
copy, never the shared relation. Named `test_migration_*.py` so CI runs it in the serial
migration pass.

Plan: documentation/plans/autocount/PLAN-do-history-backfill-13oct.md section 4.2.
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
    Path(__file__).resolve().parent / ".." / "alembic" / "versions"
    / "do_hist_0001_tracking_assumed.py"
).resolve()


def _load():
    assert MIGRATION.exists(), f"{MIGRATION} is missing"
    spec = importlib.util.spec_from_file_location("m_do_hist_0001", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _column(conn, schema):
    return conn.execute(
        sa.text(
            "SELECT is_nullable, data_type, column_default FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = 'orders' "
            "AND column_name = 'tracking_assumed_at'"
        ),
        {"s": schema},
    ).first()


def _scratch(conn):
    schema = f"zzs_dohist_{uuid.uuid4().hex[:8]}"
    conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
    conn.commit()
    conn.execute(sa.text(f"SET search_path TO {schema}"))
    conn.execute(sa.text("CREATE TABLE orders (id text PRIMARY KEY)"))
    return schema


def _drop(conn, schema):
    conn.execute(sa.text("SET search_path TO DEFAULT"))
    conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    conn.commit()


def test_revision_id_fits_and_chains_onto_a_revision():
    module = _load()
    assert module.revision == "do_hist_0001_tracking_assumed"
    assert len(module.revision) <= 32
    assert module.down_revision


def test_upgrade_adds_a_nullable_timestamptz_and_backfills_nothing():
    module = _load()
    with engine.connect() as conn:
        schema = _scratch(conn)
        try:
            conn.execute(sa.text("INSERT INTO orders (id) VALUES ('a'), ('b')"))
            _run(conn, module.upgrade)
            col = _column(conn, schema)
            assert col is not None
            assert col.is_nullable == "YES"
            assert col.data_type == "timestamp with time zone"
            assert col.column_default is None
            assert conn.execute(
                sa.text("SELECT count(*) FROM orders WHERE tracking_assumed_at IS NOT NULL")
            ).scalar() == 0
            conn.rollback()
        finally:
            _drop(conn, schema)


def test_upgrade_is_rerunnable_and_downgrade_drops_it():
    module = _load()
    with engine.connect() as conn:
        schema = _scratch(conn)
        try:
            _run(conn, module.upgrade)
            _run(conn, module.upgrade)
            assert _column(conn, schema) is not None
            _run(conn, module.downgrade)
            assert _column(conn, schema) is None
            _run(conn, module.downgrade)
            _run(conn, module.upgrade)
            assert _column(conn, schema) is not None
            conn.rollback()
        finally:
            _drop(conn, schema)
