"""Migration `sales_0003_opportunities` (S2): up and down, on a scratch schema.

Same harness as `test_migration_sales_0002_team_leader.py`: `sales_0001_teams` then
`sales_0002_team_leader` then this revision, run through a real alembic `Operations` context,
their `sales.*` DDL redirected onto a scratch schema by `schema_translate_map`, and the outer
transaction rolled back.

Nothing here exists yet on this branch: the migration file itself is missing, so every test
that loads it is expected to fail with FileNotFoundError (via `importlib.util.spec_from_file_
location` -> `spec.loader.exec_module`), which is the "missing route/file" red this lane wants.
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


def test_revision_fits_alembic_version_and_sits_on_merge_28sep_batch2():
    # Merge round re-parents S2 onto main's batch join revision from #1337.
    module = _load("sales_0003_opportunities")
    assert len(module.revision) <= 32
    assert module.down_revision == "merge_28sep_batch2"


def test_permission_slugs_are_registered_in_the_python_registry():
    from app.rbac.permission_registry import PERMISSION_REGISTRY

    slugs = {p["slug"] for p in PERMISSION_REGISTRY}
    assert {
        "sales.opportunities.view",
        "sales.opportunities.add",
        "sales.opportunities.edit",
        "sales.opportunities.delete",
    } <= slugs


def test_alembic_graph_has_one_head_and_this_revision_is_an_ancestor_of_it():
    """LESSONS 95: never assert equality with a specific head id - only "one head, and this
    revision is reachable from it"."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    module = _load("sales_0003_opportunities")
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"expected one alembic head, found {heads}"
    ancestors = {rev.revision for rev in script.walk_revisions(base="base", head=heads[0])}
    assert module.revision in ancestors


def test_upgrade_then_downgrade():
    teams = _load("sales_0001_teams")
    leader = _load("sales_0002_team_leader")
    module = _load("sales_0003_opportunities")
    scratch = f"zzs_mig_sales3_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with engine.connect() as raw:
        outer = raw.begin()
        try:
            raw.exec_driver_sql(f'CREATE SCHEMA "{scratch}"')
            conn = raw.execution_options(schema_translate_map={None: None, "sales": scratch})
            _run(conn, teams.upgrade)
            _run(conn, leader.upgrade)

            _run(conn, module.upgrade)
            insp = sa.inspect(raw)
            tables = set(insp.get_table_names(schema=scratch))
            assert {"opportunities", "opportunity_lines"} <= tables

            opp_cols = {c["name"] for c in insp.get_columns("opportunities", schema=scratch)}
            assert {
                "id",
                "company_id",
                "opportunity_no",
                "customer_id",
                "prospect_name",
                "sales_agent_id",
                "title",
                "status_id",
                "outcome",
                "expected_amount",
                "expected_close_date",
                "lost_reason",
                "sales_order_id",
                "source",
                "created_by_contact_id",
                "created_by_user_id",
                "stage_changed_at",
            } <= opp_cols
            # Round 4 (N6/N11) replaced these; the migration must not resurrect them.
            assert "product_note" not in opp_cols
            assert "product_category_id" not in opp_cols
            assert "expected_close_month" not in opp_cols

            line_cols = {
                c["name"] for c in insp.get_columns("opportunity_lines", schema=scratch)
            }
            assert {"id", "company_id", "opportunity_id", "product_id", "qty", "sort_order"} <= line_cols

            granted = raw.execute(
                sa.text(
                    "SELECT count(*) FROM user_permissions WHERE slug LIKE 'sales.opportunities.%'"
                )
            ).scalar()
            assert granted == 4

            rule = raw.execute(
                sa.text(
                    "SELECT prefix_template, number_digits FROM document_numbering_rules "
                    "WHERE doc_type = 'sales_opportunity'"
                )
            ).first()
            assert rule is not None
            assert rule[0] == "OPP-"
            assert rule[1] == 6

            # The header check constraint: neither a customer nor a prospect is refused by the
            # database itself, exercised directly rather than by name (the migration owns it).
            company = str(uuid.uuid4())
            raw.execute(
                sa.text(
                    "INSERT INTO companies (id, name, code, is_active) "
                    "VALUES (:id, 'ZZT Opp Co', :code, true)"
                ),
                {"id": company, "code": f"Z{company[:7]}"},
            )
            nested = raw.begin_nested()
            with pytest.raises(sa.exc.IntegrityError):
                raw.execute(
                    sa.text(
                        f'INSERT INTO "{scratch}".opportunities '
                        "(id, company_id, opportunity_no, title, outcome, expected_amount, "
                        "expected_close_date, source) VALUES "
                        "(:id, :c, 'OPP-000001', 'ZZT No Customer Or Prospect', 'open', 1, "
                        "current_date, 'crm')"
                    ),
                    {"id": str(uuid.uuid4()), "c": company},
                )
            nested.rollback()

            _run(conn, module.downgrade)
            insp = sa.inspect(raw)
            assert set(insp.get_table_names(schema=scratch)) == {"teams", "team_members"}
            # Downgrade leaves the numbering rule and the permission slugs (section 16).
            still_rule = raw.execute(
                sa.text(
                    "SELECT count(*) FROM document_numbering_rules "
                    "WHERE doc_type = 'sales_opportunity'"
                )
            ).scalar()
            assert still_rule == 1
            still_granted = raw.execute(
                sa.text(
                    "SELECT count(*) FROM user_permissions WHERE slug LIKE 'sales.opportunities.%'"
                )
            ).scalar()
            assert still_granted == 4
        finally:
            outer.rollback()
