"""Migration `sales_0002_team_leader` (S6 fix lane round 2, W1): up and down, on a scratch schema.

Same harness as `test_migration_sales_0001_teams.py`: `sales_0001_teams` then this revision run
through a real alembic `Operations` context, their `sales.*` DDL redirected onto a scratch schema
by `schema_translate_map`, and the outer transaction rolled back.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database import engine

VERSIONS = (Path(__file__).resolve().parent / ".." / "alembic" / "versions").resolve()


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"m_{name}", VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        fn()


def test_revision_fits_alembic_version_and_sits_on_sales_0001_teams():
    module = _load("sales_0002_team_leader")
    assert len(module.revision) <= 32
    assert module.down_revision == "sales_0001_teams"


def _triggers(raw, schema):
    return set(
        raw.execute(
            sa.text(
                "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND NOT t.tgisinternal"
            ),
            {"s": schema},
        ).scalars()
    )


def test_upgrade_then_downgrade():
    first = _load("sales_0001_teams")
    module = _load("sales_0002_team_leader")
    scratch = f"zzs_mig_sales2_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with engine.connect() as raw:
        outer = raw.begin()
        try:
            raw.exec_driver_sql(f'CREATE SCHEMA "{scratch}"')
            conn = raw.execution_options(schema_translate_map={None: None, "sales": scratch})
            _run(conn, first.upgrade)

            _run(conn, module.upgrade)
            insp = sa.inspect(raw)
            cols = {c["name"]: c for c in insp.get_columns("teams", schema=scratch)}
            assert cols["leader_sales_agent_id"]["nullable"] is True
            fks = {
                fk["name"]: fk for fk in insp.get_foreign_keys("teams", schema=scratch)
            }
            fk = fks["fk_sales_teams_leader_sales_agent_id"]
            assert fk["referred_table"] == "sales_agents"
            assert fk["options"].get("ondelete") == "SET NULL"
            assert _triggers(raw, scratch) == {
                "trg_sales_teams_leader_is_member",
                "trg_sales_team_members_leader_is_member",
            }

            # The rule itself, on the scratch tables.
            # CI's database has no data (LESSONS-LEARNT): the company comes from this test.
            company = str(uuid.uuid4())
            raw.execute(
                sa.text(
                    "INSERT INTO companies (id, name, code, is_active) "
                    "VALUES (:id, 'ZZT Lead Co', :code, true)"
                ),
                {"id": company, "code": f"Z{company[:7]}"},
            )
            agent = str(uuid.uuid4())
            raw.execute(
                sa.text(
                    "INSERT INTO sales_agents (id, sales_agent, description, is_active) "
                    "VALUES (:id, :code, 'ZZT Lead', true)"
                ),
                {"id": agent, "code": f"ZZT{agent[:8]}"},
            )
            team = str(uuid.uuid4())
            # Inside the savepoint, so its queued trigger event goes with the rollback and
            # the downgrade's ALTER TABLE meets no pending events.
            nested = raw.begin_nested()
            raw.execute(
                sa.text(
                    f'INSERT INTO "{scratch}".teams (id, company_id, name, leader_sales_agent_id) '
                    "VALUES (:id, :c, 'ZZT Lead', :a)"
                ),
                {"id": team, "c": company, "a": agent},
            )
            with pytest.raises(sa.exc.IntegrityError, match="leader"):
                raw.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
            nested.rollback()

            _run(conn, module.downgrade)
            insp = sa.inspect(raw)
            cols = {c["name"] for c in insp.get_columns("teams", schema=scratch)}
            assert "leader_sales_agent_id" not in cols
            assert _triggers(raw, scratch) == set()
            assert (
                raw.execute(
                    sa.text(
                        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
                        "ON n.oid = p.pronamespace WHERE n.nspname = :s"
                    ),
                    {"s": scratch},
                ).scalar()
                == 0
            )
        finally:
            outer.rollback()
