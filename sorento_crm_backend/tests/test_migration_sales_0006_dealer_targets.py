"""Migration `sales_0006_dealer_targets` (lane SALES-TARGET-DEALER, UAC D2-8).

Same harness as `test_migration_sales_0005_commission_tiers.py`: the sales revisions up to this
one run on a scratch copy of the `sales` schema inside one outer transaction that is rolled back.
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

_EARLIER = (
    "sales_0001_teams",
    "sales_0002_team_leader",
    "sales_0003_targets",
    "sales_0004_target_brands",
    "sales_0005_commission_tiers",
)


@contextmanager
def _upgraded(*, seed=None):
    mods = [_load(n) for n in _EARLIER]
    m6 = _load("sales_0006_dealer_targets")
    scratch = f"zzs_mig_sales6_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with engine.connect() as raw:
        outer = raw.begin()
        try:
            raw.exec_driver_sql(f'CREATE SCHEMA "{scratch}"')
            conn = raw.execution_options(schema_translate_map={"sales": scratch})
            for m in mods:
                _run(conn, m.upgrade)
            seeded = seed(raw, scratch) if seed else None
            _run(conn, m6.upgrade)
            yield raw, conn, scratch, m6, seeded
        finally:
            outer.rollback()


def _seed_old_target(raw, scratch):
    company = _seed_company(raw)
    agent = _seed_agent(raw, company)
    target = _insert_target(raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent)
    raw.execute(
        sa.text(
            f'INSERT INTO "{scratch}".target_commission_tiers (id, company_id, target_id, from_pct, rate) '
            "VALUES (:id, :c, :t, 100, 2)"
        ),
        {"id": str(uuid.uuid4()), "c": company, "t": target},
    )
    return company, agent, target


def test_revision_chains_and_fits():
    m6 = _load("sales_0006_dealer_targets")
    assert m6.revision == "sales_0006_dealer_targets"
    assert m6.down_revision == "sds_0001_stock_debt_snapshots"
    assert len(m6.revision) <= 32


def test_existing_targets_take_the_old_meaning():
    with _upgraded(seed=_seed_old_target) as (raw, conn, scratch, _, seeded):
        _, _, target = seeded
        row = raw.execute(
            sa.text(
                f'SELECT threshold_unit, rounding, tiers_per_period, payout_customer_id '
                f'FROM "{scratch}".targets WHERE id = :t'
            ),
            {"t": target},
        ).one()
        assert tuple(row) == ("pct", "sen", False, None)
        tier = raw.execute(
            sa.text(
                f'SELECT from_pct, threshold, is_additional, period_start '
                f'FROM "{scratch}".target_commission_tiers WHERE target_id = :t'
            ),
            {"t": target},
        ).one()
        assert (float(tier[0]), tier[1], tier[2], tier[3]) == (100.0, None, False, None)


def test_dealer_subject_and_checks():
    with _upgraded() as (raw, conn, scratch, _, _seeded):
        company = _seed_company(raw)
        checks = _check_constraints(raw, scratch, "targets")
        assert "dealer" in checks["ck_sales_targets_subject_kind"]
        target = _insert_target(raw, scratch, company_id=company, subject_kind="dealer")
        assert target
        agent = _seed_agent(raw, company)
        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_subject"):
            with raw.begin_nested():
                _insert_target(raw, scratch, company_id=company, subject_kind="dealer", sales_agent_id=agent)
        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_threshold_unit"):
            with raw.begin_nested():
                raw.execute(
                    sa.text(f"UPDATE \"{scratch}\".targets SET threshold_unit = 'coins' WHERE id = :t"),
                    {"t": target},
                )
        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_target_commission_tiers_one_threshold"):
            with raw.begin_nested():
                raw.execute(
                    sa.text(
                        f'INSERT INTO "{scratch}".target_commission_tiers '
                        "(id, company_id, target_id, from_pct, threshold, rate) VALUES (:id, :c, :t, 1, 1, 1)"
                    ),
                    {"id": str(uuid.uuid4()), "c": company, "t": target},
                )


def test_downgrade_round_trip():
    with _upgraded(seed=_seed_old_target) as (raw, conn, scratch, m6, seeded):
        _run(conn, m6.downgrade)
        tables = {
            r[0]
            for r in raw.execute(
                sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
                {"s": scratch},
            )
        }
        assert not {"target_accounts", "target_tier_rates", "point_ratios"} & tables
        assert raw.execute(sa.text(f'SELECT count(*) FROM "{scratch}".target_commission_tiers')).scalar() == 1
