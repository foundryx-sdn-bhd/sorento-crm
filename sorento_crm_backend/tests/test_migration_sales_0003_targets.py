"""Migration `sales_0003_targets` (S1; plan 3.1, 16.1): up and down, on a scratch schema.

Same harness as `test_migration_sales_0002_team_leader.py`: `sales_0001_teams`, then
`sales_0002_team_leader`, then this revision, run through a real alembic `Operations` context
with the `sales` schema's DDL redirected onto a scratch copy by `schema_translate_map`. The DO
seam column lands on `public.order_lines` (unmapped, the real core table) so it is exercised in
the SAME outer transaction, which is rolled back at teardown.

`sales_0003_targets` does not exist yet, so `_load` raises `FileNotFoundError` inside each test
body (never at collection): the right kind of red, one failure per test.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from contextlib import contextmanager
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


@contextmanager
def _upgraded():
    """`sales_0001`, `sales_0002`, `sales_0003` upgraded onto one scratch `sales` schema.

    The outer transaction (covering both the scratch schema's DDL and the DO seam's ALTER on
    the real `public.order_lines`) is rolled back on exit, so nothing here survives the test.
    """
    m1 = _load("sales_0001_teams")
    m2 = _load("sales_0002_team_leader")
    m3 = _load("sales_0003_targets")  # missing until the coder writes it
    scratch = f"zzs_mig_sales3_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with engine.connect() as raw:
        outer = raw.begin()
        try:
            raw.exec_driver_sql(f'CREATE SCHEMA "{scratch}"')
            conn = raw.execution_options(schema_translate_map={None: None, "sales": scratch})
            _run(conn, m1.upgrade)
            _run(conn, m2.upgrade)
            _run(conn, m3.upgrade)
            yield raw, conn, scratch, m3
        finally:
            outer.rollback()


def _check_constraints(raw, schema: str, table: str) -> dict:
    rows = raw.execute(
        sa.text(
            "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
            "WHERE contype = 'c' AND conrelid = (quote_ident(:s) || '.' || quote_ident(:t))::regclass"
        ),
        {"s": schema, "t": table},
    ).all()
    return {r.conname: r.definition for r in rows}


# --------------------------------------------------------------------------------------- #
# seed helpers - real FK targets, ZZT-prefixed, all inside the rolled-back outer transaction
# --------------------------------------------------------------------------------------- #


def _seed_company(raw) -> str:
    company = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO companies (id, name, code, is_active) VALUES (:id, 'ZZT Co', :code, true)"
        ),
        {"id": company, "code": f"Z{company[:7]}"},
    )
    return company


def _seed_agent(raw, company: str | None = None) -> str:
    agent = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO sales_agents (id, sales_agent, description, is_active, company_id) "
            "VALUES (:id, :code, 'ZZT Agent', true, :c)"
        ),
        {"id": agent, "code": f"ZZT{agent[:8]}", "c": company},
    )
    return agent


def _seed_team(raw, scratch: str, company: str) -> str:
    team = str(uuid.uuid4())
    raw.execute(
        sa.text(f'INSERT INTO "{scratch}".teams (id, company_id, name) VALUES (:id, :c, :n)'),
        {"id": team, "c": company, "n": f"ZZT {team[:6]}"},
    )
    return team


def _seed_category(raw, company: str) -> str:
    category = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO product_categories (id, company_id, category_code, category_name) "
            "VALUES (:id, :c, :code, 'ZZT Cat')"
        ),
        {"id": category, "c": company, "code": f"ZZT{category[:8]}"},
    )
    return category


def _seed_product(raw, company: str, category: str) -> str:
    uom = str(uuid.uuid4())
    raw.execute(
        sa.text("INSERT INTO units_of_measure (id, uom_code, uom_name) VALUES (:id, :c, 'ZZT UOM')"),
        {"id": uom, "c": f"ZZT{uom[:8]}"},
    )
    product = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO products (id, company_id, product_code, product_name, category_id, "
            "base_uom_id, list_price) VALUES (:id, :c, :code, 'ZZT Product', :cat, :uom, 0)"
        ),
        {"id": product, "c": company, "code": f"ZZT{product[:8]}", "cat": category, "uom": uom},
    )
    return product


def _seed_warehouse(raw, company: str) -> str:
    wh = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO warehouses (id, company_id, warehouse_code, warehouse_name, is_active) "
            "VALUES (:id, :c, :code, 'ZZT WH', true)"
        ),
        {"id": wh, "c": company, "code": f"ZZT{wh[:8]}"},
    )
    return wh


def _seed_sales_order_line(raw, company: str, product: str) -> tuple[str, str]:
    so = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO sales_orders (id, company_id, so_number, order_date, status) "
            "VALUES (:id, :c, :n, '2026-10-05', 'open')"
        ),
        {"id": so, "c": company, "n": f"ZZT{so[:8]}"},
    )
    line = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO sales_order_lines (id, company_id, sales_order_id, product_id, "
            "qty_ordered, qty_delivered, line_total, line_status) "
            "VALUES (:id, :c, :so, :p, 10, 0, 1000, 'open')"
        ),
        {"id": line, "c": company, "so": so, "p": product},
    )
    return so, line


def _seed_do_line(raw, company: str, product: str, warehouse: str, sales_order_line_id: str) -> str:
    # `orders` has several NOT NULL columns with only a Python-side ORM `default=`
    # (kpi_warning, the four amount columns, synced_to_excel) - no `server_default`, so a raw
    # SQL insert must state them itself.
    order = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO orders (id, company_id, order_number, order_date, is_cancelled, "
            "kpi_warning, subtotal_amount, discount_amount, tax_amount, total_amount, "
            "synced_to_excel) "
            "VALUES (:id, :c, :n, '2026-10-06', false, false, 0, 0, 0, 0, false)"
        ),
        {"id": order, "c": company, "n": f"ZZT{order[:8]}"},
    )
    line = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO order_lines (id, company_id, line_sequence, order_id, product_id, "
            "warehouse_id, quantity, sales_order_line_id) VALUES (:id, :c, 1, :o, :p, :w, 5, :sol)"
        ),
        {"id": line, "c": company, "o": order, "p": product, "w": warehouse, "sol": sales_order_line_id},
    )
    return line


def _insert_target(raw, scratch: str, **kw) -> str:
    kw.setdefault("id", str(uuid.uuid4()))
    kw.setdefault("target_no", f"TGT-{kw['id'][:6]}")
    kw.setdefault("name", "ZZT target")
    kw.setdefault("metric", "amount")
    kw.setdefault("basis", "ordered")
    kw.setdefault("product_scope", "all")
    kw.setdefault("start_date", "2026-10-01")
    kw.setdefault("end_date", "2026-10-31")
    cols = ", ".join(kw.keys())
    placeholders = ", ".join(f":{k}" for k in kw)
    raw.execute(
        sa.text(f'INSERT INTO "{scratch}".targets ({cols}) VALUES ({placeholders})'), kw
    )
    return kw["id"]


# --------------------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------------------- #


def test_upgrade_creates_tables_constraints_indexes():
    with _upgraded() as (raw, conn, scratch, m3):
        insp = sa.inspect(raw)
        target_cols = {c["name"] for c in insp.get_columns("targets", schema=scratch)}
        assert {
            "id", "company_id", "target_no", "name", "subject_kind", "sales_agent_id",
            "sales_team_id", "metric", "basis", "product_scope", "start_date", "end_date",
            "split_every", "split_unit", "parent_target_id", "created_by_user_id",
            "created_at", "updated_at",
        } <= target_cols
        # S4 adds commission_method with its tiers, not S1.
        assert "commission_method" not in target_cols

        period_cols = {c["name"] for c in insp.get_columns("target_periods", schema=scratch)}
        assert {
            "id", "company_id", "target_id", "period_start", "period_end", "target_value",
        } <= period_cols

        scope_cols = {c["name"] for c in insp.get_columns("target_scope", schema=scratch)}
        assert {
            "id", "company_id", "target_id", "product_category_id", "product_id",
        } <= scope_cols

        target_indexes = insp.get_indexes("targets", schema=scratch)
        for col in ("company_id", "sales_agent_id", "sales_team_id", "parent_target_id"):
            assert any(col in i["column_names"] for i in target_indexes), col
        unique_names = {i["name"] for i in target_indexes if i.get("unique")}
        assert "uq_sales_targets_company_target_no" in unique_names

        period_unique = {
            i["name"] for i in insp.get_indexes("target_periods", schema=scratch) if i.get("unique")
        }
        assert "uq_sales_target_periods_target_start" in period_unique

        checks = _check_constraints(raw, scratch, "targets")
        for name in (
            "ck_sales_targets_subject_kind", "ck_sales_targets_subject", "ck_sales_targets_metric",
            "ck_sales_targets_basis", "ck_sales_targets_product_scope", "ck_sales_targets_dates",
            "ck_sales_targets_split_every", "ck_sales_targets_split_unit",
            "ck_sales_targets_split_pair", "ck_sales_targets_parent_agent",
        ):
            assert name in checks, name

        period_checks = _check_constraints(raw, scratch, "target_periods")
        assert "ck_sales_target_periods_dates" in period_checks

        scope_checks = _check_constraints(raw, scratch, "target_scope")
        assert "ck_sales_target_scope_one" in scope_checks


def test_subject_and_parent_checks():
    with _upgraded() as (raw, conn, scratch, m3):
        company = _seed_company(raw)
        agent = _seed_agent(raw, company)
        team = _seed_team(raw, scratch, company)

        good = _insert_target(
            raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent
        )

        # agent AND team both set: ck_sales_targets_subject.
        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_subject"):
            with raw.begin_nested():
                _insert_target(
                    raw, scratch, company_id=company, subject_kind="agent",
                    sales_agent_id=agent, sales_team_id=team,
                )

        # team subject with no team set: ck_sales_targets_subject.
        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_subject"):
            with raw.begin_nested():
                _insert_target(raw, scratch, company_id=company, subject_kind="team")

        # an unsupported subject_kind (S7 adds "dealer", not S1): ck_sales_targets_subject%.
        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_subject"):
            with raw.begin_nested():
                _insert_target(
                    raw, scratch, company_id=company, subject_kind="dealer", sales_agent_id=agent,
                )

        # parent_target_id set on a TEAM target: ck_sales_targets_parent_agent (parent implies
        # subject_kind agent).
        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_parent_agent"):
            with raw.begin_nested():
                _insert_target(
                    raw, scratch, company_id=company, subject_kind="team",
                    sales_team_id=team, parent_target_id=good,
                )

        # parent_target_id set on an agent target: fine (a team's child).
        _insert_target(
            raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent,
            parent_target_id=good,
        )


def test_split_pair_and_dates_checks():
    with _upgraded() as (raw, conn, scratch, m3):
        company = _seed_company(raw)
        agent = _seed_agent(raw, company)

        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_dates"):
            with raw.begin_nested():
                _insert_target(
                    raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent,
                    start_date="2026-10-31", end_date="2026-10-01",
                )

        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_split_pair"):
            with raw.begin_nested():
                _insert_target(
                    raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent,
                    split_every=1,
                )

        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_split_pair"):
            with raw.begin_nested():
                _insert_target(
                    raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent,
                    split_unit="month",
                )

        for bad in (0, 100):
            with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_split_every"):
                with raw.begin_nested():
                    _insert_target(
                        raw, scratch, company_id=company, subject_kind="agent",
                        sales_agent_id=agent, split_every=bad, split_unit="day",
                    )

        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_targets_split_unit"):
            with raw.begin_nested():
                _insert_target(
                    raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent,
                    split_every=1, split_unit="quarter",
                )

        # a valid split passes.
        _insert_target(
            raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent,
            split_every=2, split_unit="week",
        )


def test_scope_row_exactly_one():
    with _upgraded() as (raw, conn, scratch, m3):
        company = _seed_company(raw)
        agent = _seed_agent(raw, company)
        category = _seed_category(raw, company)
        product = _seed_product(raw, company, category)
        target = _insert_target(
            raw, scratch, company_id=company, subject_kind="agent", sales_agent_id=agent
        )

        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_target_scope_one"):
            with raw.begin_nested():
                raw.execute(
                    sa.text(
                        f'INSERT INTO "{scratch}".target_scope (id, company_id, target_id) '
                        "VALUES (:id, :c, :t)"
                    ),
                    {"id": str(uuid.uuid4()), "c": company, "t": target},
                )

        with pytest.raises(sa.exc.IntegrityError, match="ck_sales_target_scope_one"):
            with raw.begin_nested():
                raw.execute(
                    sa.text(
                        f'INSERT INTO "{scratch}".target_scope '
                        "(id, company_id, target_id, product_category_id, product_id) "
                        "VALUES (:id, :c, :t, :cat, :p)"
                    ),
                    {"id": str(uuid.uuid4()), "c": company, "t": target, "cat": category, "p": product},
                )

        raw.execute(
            sa.text(
                f'INSERT INTO "{scratch}".target_scope (id, company_id, target_id, product_category_id) '
                "VALUES (:id, :c, :t, :cat)"
            ),
            {"id": str(uuid.uuid4()), "c": company, "t": target, "cat": category},
        )


def test_do_seam_column_set_null_on_so_line_delete():
    with _upgraded() as (raw, conn, scratch, m3):
        insp = sa.inspect(raw)
        cols = {c["name"]: c for c in insp.get_columns("order_lines")}
        assert "sales_order_line_id" in cols
        assert cols["sales_order_line_id"]["nullable"] is True
        fks = {fk["name"]: fk for fk in insp.get_foreign_keys("order_lines")}
        fk = fks["fk_order_lines_sales_order_line_id"]
        assert fk["referred_table"] == "sales_order_lines"
        assert fk["options"].get("ondelete") == "SET NULL"
        assert ["sales_order_line_id"] in [i["column_names"] for i in insp.get_indexes("order_lines")]

        company = _seed_company(raw)
        category = _seed_category(raw, company)
        product = _seed_product(raw, company, category)
        warehouse = _seed_warehouse(raw, company)
        so, sol = _seed_sales_order_line(raw, company, product)
        do_line = _seed_do_line(raw, company, product, warehouse, sol)

        raw.execute(sa.text("DELETE FROM sales_order_lines WHERE id = :id"), {"id": sol})
        left = raw.execute(
            sa.text("SELECT sales_order_line_id FROM order_lines WHERE id = :id"), {"id": do_line}
        ).scalar()
        assert left is None


def test_order_date_index():
    with _upgraded() as (raw, conn, scratch, m3):
        insp = sa.inspect(raw)
        idx_cols = [i["column_names"] for i in insp.get_indexes("sales_orders")]
        assert ["order_date"] in idx_cols


def test_targets_slugs_granted_admin_superadmin():
    with _upgraded() as (raw, conn, scratch, m3):
        slugs = {
            "sales.targets.view", "sales.targets.add", "sales.targets.edit", "sales.targets.delete",
        }
        found = set(
            raw.execute(
                sa.text("SELECT slug FROM user_permissions WHERE slug = ANY(:s)"), {"s": list(slugs)}
            ).scalars()
        )
        assert found == slugs
        for slug in slugs:
            roles = set(
                raw.execute(
                    sa.text(
                        "SELECT r.slug FROM user_role_permissions urp "
                        "JOIN user_roles r ON r.id = urp.role_id "
                        "JOIN user_permissions p ON p.id = urp.permission_id "
                        "WHERE p.slug = :slug"
                    ),
                    {"slug": slug},
                ).scalars()
            )
            assert {"admin", "superadmin"} <= roles, slug


def test_downgrade_keeps_schema_and_permissions():
    m1 = _load("sales_0001_teams")
    m2 = _load("sales_0002_team_leader")
    m3 = _load("sales_0003_targets")
    scratch = f"zzs_mig_sales3d_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with engine.connect() as raw:
        outer = raw.begin()
        try:
            raw.exec_driver_sql(f'CREATE SCHEMA "{scratch}"')
            conn = raw.execution_options(schema_translate_map={None: None, "sales": scratch})
            _run(conn, m1.upgrade)
            _run(conn, m2.upgrade)
            _run(conn, m3.upgrade)

            _run(conn, m3.downgrade)
            insp = sa.inspect(raw)
            tables = set(insp.get_table_names(schema=scratch))
            assert not {"targets", "target_periods", "target_scope"} & tables
            # The schema (a namespace, ADR-0011) and S6's own tables survive.
            assert {"teams", "team_members"} <= tables

            cols = {c["name"] for c in insp.get_columns("order_lines")}
            assert "sales_order_line_id" not in cols
            idx_cols = [i["column_names"] for i in insp.get_indexes("sales_orders")]
            assert ["order_date"] not in idx_cols

            found = set(
                raw.execute(
                    sa.text("SELECT slug FROM user_permissions WHERE slug LIKE 'sales.targets.%'")
                ).scalars()
            )
            assert found == {
                "sales.targets.view", "sales.targets.add", "sales.targets.edit",
                "sales.targets.delete",
            }
        finally:
            outer.rollback()
