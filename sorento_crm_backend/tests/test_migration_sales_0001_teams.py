"""Migration `sales_0001_teams` (S6): up and down, on a scratch schema, rolled back.

The migration's own `upgrade()` and `downgrade()` run through a real alembic `Operations`
context. Its `sales.*` DDL is redirected onto a scratch schema by `schema_translate_map`
(lesson 114: a rolled-back transaction is not isolation for DDL on the shared relations), and
everything else it writes (permission rows, grants, the catalog row) sits in the outer
transaction, which is rolled back.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database import engine

MIGRATION = (
    Path(__file__).resolve().parent / ".." / "alembic" / "versions" / "sales_0001_teams.py"
).resolve()


def _load():
    spec = importlib.util.spec_from_file_location("m_sales_0001", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        fn()


def test_revision_id_fits_alembic_version_and_chains_onto_one_head():
    module = _load()
    assert len(module.revision) <= 32
    assert module.down_revision


def test_upgrade_then_downgrade():
    module = _load()
    scratch = f"zzs_mig_sales_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with engine.connect() as raw:
        outer = raw.begin()
        try:
            raw.exec_driver_sql(f'CREATE SCHEMA "{scratch}"')
            conn = raw.execution_options(schema_translate_map={None: None, "sales": scratch})

            _run(conn, module.upgrade)
            insp = sa.inspect(raw)
            assert set(insp.get_table_names(schema=scratch)) == {"teams", "team_members"}
            for table in ("teams", "team_members"):
                # Security review: the database is where an owned table's company is enforced
                # (CompanyScopedMixin); a NULL-company row would be invisible to everyone and
                # escape the per-company unique name.
                cols = {c["name"]: c for c in insp.get_columns(table, schema=scratch)}
                assert cols["company_id"]["nullable"] is False, table
            member_cols = {c["name"] for c in insp.get_columns("team_members", schema=scratch)}
            assert {"valid_from", "valid_to", "sales_team_id", "sales_agent_id"} <= member_cols
            indexes = {i["name"] for i in insp.get_indexes("team_members", schema=scratch)}
            assert "uq_sales_team_members_open" in indexes
            team_indexes = {i["name"] for i in insp.get_indexes("teams", schema=scratch)}
            assert "uq_sales_teams_company_lower_name" in team_indexes

            granted = raw.execute(
                sa.text(
                    "SELECT count(*) FROM user_permissions WHERE slug LIKE 'sales.teams.%'"
                )
            ).scalar()
            assert granted == 4
            catalog = raw.execute(
                sa.text("SELECT count(*) FROM app_modules_catalog WHERE module_key = 'sales'")
            ).scalar()
            assert catalog == 1

            _run(conn, module.downgrade)
            insp = sa.inspect(raw)
            assert insp.get_table_names(schema=scratch) == []
            assert (
                raw.execute(
                    sa.text("SELECT count(*) FROM app_modules_catalog WHERE module_key = 'sales'")
                ).scalar()
                == 0
            )
        finally:
            outer.rollback()
