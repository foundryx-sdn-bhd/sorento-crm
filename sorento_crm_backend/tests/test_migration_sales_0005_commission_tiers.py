"""Migration `sales_0005_commission_tiers` (S1 fix lane round 3, F2: commission tiers).

Same harness as `test_migration_sales_0003_targets.py`: the sales revisions up to this one run
on a scratch copy of the `sales` schema inside one outer transaction that is rolled back.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

from app.database import engine

from .test_migration_sales_0003_targets import (
    _check_constraints,
    _insert_target,
    _load,
    _run,
    _seed_agent,
    _seed_company,
)

_EARLIER = ("sales_0001_teams", "sales_0002_team_leader", "sales_0003_targets", "sales_0004_target_brands")


@contextmanager
def _upgraded():
    mods = [_load(n) for n in _EARLIER]
    m5 = _load("sales_0005_commission_tiers")
    scratch = f"zzs_mig_sales5_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with engine.connect() as raw:
        outer = raw.begin()
        try:
            raw.exec_driver_sql(f'CREATE SCHEMA "{scratch}"')
            conn = raw.execution_options(schema_translate_map={None: None, "sales": scratch})
            for m in mods:
                _run(conn, m.upgrade)
            _run(conn, m5.upgrade)
            yield raw, conn, scratch, m5
        finally:
            outer.rollback()


def _tier(raw, scratch, company, target, from_pct, rate=1):
    raw.execute(
        sa.text(
            f'INSERT INTO "{scratch}".target_commission_tiers (id, company_id, target_id, from_pct, rate) '
            "VALUES (:id, :c, :t, :f, :r)"
        ),
        {"id": str(uuid.uuid4()), "c": company, "t": target, "f": from_pct, "r": rate},
    )


def test_revision_chains_on_sales_0004():
    m5 = _load("sales_0005_commission_tiers")
    assert m5.revision == "sales_0005_commission_tiers"
    assert m5.down_revision == "sales_0004_target_brands"
    assert len(m5.revision) <= 32


def test_method_defaults_to_none_and_is_checked():
    with _upgraded() as (raw, conn, scratch, _):
        company = _seed_company(raw)
        agent = _seed_agent(raw, company)
        target = _insert_target(raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent)
        method = raw.execute(
            sa.text(f'SELECT commission_method FROM "{scratch}".targets WHERE id = :t'), {"t": target}
        ).scalar()
        assert method == "none"
        assert "retroactive" in _check_constraints(raw, scratch, "targets")["ck_sales_targets_commission_method"]
        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_commission_method"):
            with raw.begin_nested():
                raw.execute(
                    sa.text(f"UPDATE \"{scratch}\".targets SET commission_method = 'flat' WHERE id = :t"),
                    {"t": target},
                )


def test_tiers_unique_per_from_pct_and_cascade_with_the_target():
    with _upgraded() as (raw, conn, scratch, _):
        company = _seed_company(raw)
        agent = _seed_agent(raw, company)
        target = _insert_target(raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent)
        _tier(raw, scratch, company, target, 0)
        with pytest.raises(sa.exc.IntegrityError, match="uq_sales_target_commission_tiers_target_from"):
            with raw.begin_nested():
                _tier(raw, scratch, company, target, 0, rate=2)
        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_target_commission_tiers_from_pct"):
            with raw.begin_nested():
                _tier(raw, scratch, company, target, -1)
        raw.execute(sa.text(f'DELETE FROM "{scratch}".targets WHERE id = :t'), {"t": target})
        left = raw.execute(sa.text(f'SELECT count(*) FROM "{scratch}".target_commission_tiers')).scalar()
        assert left == 0


def test_downgrade_drops_the_table_and_the_column():
    with _upgraded() as (raw, conn, scratch, m5):
        _run(conn, m5.downgrade)
        tables = {
            r[0]
            for r in raw.execute(
                sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
                {"s": scratch},
            )
        }
        assert "target_commission_tiers" not in tables
        cols = {
            r[0]
            for r in raw.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'targets'"
                ),
                {"s": scratch},
            )
        }
        assert "commission_method" not in cols
