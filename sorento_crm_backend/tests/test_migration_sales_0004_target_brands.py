"""Migration `sales_0004_target_brands` (S1 fix lane round 2, F1: Applies to gains Brand).

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
    _seed_category,
    _seed_company,
)


@contextmanager
def _upgraded():
    mods = [_load(n) for n in ("sales_0001_teams", "sales_0002_team_leader", "sales_0003_targets")]
    m4 = _load("sales_0004_target_brands")
    scratch = f"zzs_mig_sales4_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with engine.connect() as raw:
        outer = raw.begin()
        try:
            raw.exec_driver_sql(f'CREATE SCHEMA "{scratch}"')
            conn = raw.execution_options(schema_translate_map={None: None, "sales": scratch})
            for m in mods:
                _run(conn, m.upgrade)
            _run(conn, m4.upgrade)
            yield raw, conn, scratch, m4
        finally:
            outer.rollback()


def _seed_brand(raw, company: str) -> str:
    brand = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO brands (id, company_id, brand_code, brand_name) "
            "VALUES (:id, :c, :code, 'ZZT Brand')"
        ),
        {"id": brand, "c": company, "code": f"ZB{brand[:8]}"},
    )
    return brand


def test_revision_chains_on_sales_0003():
    m4 = _load("sales_0004_target_brands")
    assert m4.revision == "sales_0004_target_brands"
    assert len(m4.revision) <= 32
    assert m4.down_revision == "sales_0003_targets"


def test_brand_scope_row_and_brands_product_scope():
    with _upgraded() as (raw, conn, scratch, m4):
        checks = _check_constraints(raw, scratch, "target_scope")
        assert "brand_id" in checks["ck_sales_target_scope_one"]
        assert "brands" in _check_constraints(raw, scratch, "targets")["ck_sales_targets_product_scope"]

        company = _seed_company(raw)
        agent = _seed_agent(raw, company)
        brand = _seed_brand(raw, company)
        category = _seed_category(raw, company)
        target = _insert_target(
            raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent,
            product_scope="brands",
        )
        raw.execute(
            sa.text(
                f'INSERT INTO "{scratch}".target_scope (id, company_id, target_id, brand_id) '
                "VALUES (:id, :c, :t, :b)"
            ),
            {"id": str(uuid.uuid4()), "c": company, "t": target, "b": brand},
        )
        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_target_scope_one"):
            with raw.begin_nested():
                raw.execute(
                    sa.text(
                        f'INSERT INTO "{scratch}".target_scope '
                        "(id, company_id, target_id, product_category_id, brand_id) "
                        "VALUES (:id, :c, :t, :cat, :b)"
                    ),
                    {"id": str(uuid.uuid4()), "c": company, "t": target, "cat": category, "b": brand},
                )

        # Deleting the brand takes its scope rows with it.
        raw.execute(sa.text("DELETE FROM brands WHERE id = :b"), {"b": brand})
        left = raw.execute(
            sa.text(f'SELECT count(*) FROM "{scratch}".target_scope WHERE target_id = :t'),
            {"t": target},
        ).scalar()
        assert left == 0


def test_downgrade_removes_brand_rows_and_column():
    with _upgraded() as (raw, conn, scratch, m4):
        company = _seed_company(raw)
        agent = _seed_agent(raw, company)
        brand = _seed_brand(raw, company)
        target = _insert_target(
            raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent,
            product_scope="brands",
        )
        raw.execute(
            sa.text(
                f'INSERT INTO "{scratch}".target_scope (id, company_id, target_id, brand_id) '
                "VALUES (:id, :c, :t, :b)"
            ),
            {"id": str(uuid.uuid4()), "c": company, "t": target, "b": brand},
        )
        _run(conn, m4.downgrade)
        cols = {
            r[0]
            for r in raw.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'target_scope'"
                ),
                {"s": scratch},
            )
        }
        assert "brand_id" not in cols
        # A brand target falls back to All products rather than breaking the old check.
        scope = raw.execute(
            sa.text(f'SELECT product_scope FROM "{scratch}".targets WHERE id = :t'), {"t": target}
        ).scalar()
        assert scope == "all"
        assert "brands" not in _check_constraints(raw, scratch, "targets")["ck_sales_targets_product_scope"]
