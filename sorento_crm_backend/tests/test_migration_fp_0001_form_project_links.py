"""FORMS-PROJECT: stock inquiries, price tag requests and sales opportunities gain a nullable
project link (FK to projects.projects, ON DELETE SET NULL, indexed).

The scratch schema is built from ``Base.metadata``, so the column checks prove the MODELS
declare the link. The migration's own ``upgrade()`` then runs against that schema after the
columns are dropped, proving it re-adds them idempotently (twice) with the FK and index.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from ._pg_fixture import blank_session

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "fp_0001_form_project_links.py"
)

# (module schema suffix or None for the default schema, table)
TABLES = ((None, "stock_inquiries"), (None, "price_tag_requests"), ("sales", "opportunities"))


def _load():
    spec = importlib.util.spec_from_file_location("migration_fp_0001", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _qualified(db, module):
    current = db.execute(text("select current_schema()")).scalar()
    if module is None:
        return f'"{current}"'
    return f'"{module}"' if current in (None, "public") else f'"{current}_{module}"'


def _fk_delete_rule(db, schema: str, table: str):
    return db.execute(
        text(
            """
            select rc.delete_rule
            from information_schema.table_constraints tc
            join information_schema.key_column_usage kcu
              on kcu.constraint_name = tc.constraint_name and kcu.table_schema = tc.table_schema
            join information_schema.referential_constraints rc
              on rc.constraint_name = tc.constraint_name and rc.constraint_schema = tc.table_schema
            where tc.constraint_type = 'FOREIGN KEY' and tc.table_schema = :s
              and tc.table_name = :t and kcu.column_name = 'project_id'
            """
        ),
        {"s": schema.strip('"'), "t": table},
    ).scalar()


def _has_index(db, schema: str, table: str) -> bool:
    return bool(
        db.execute(
            text(
                "select 1 from pg_indexes where schemaname = :s and tablename = :t "
                "and indexdef ilike '%(project_id)%'"
            ),
            {"s": schema.strip('"'), "t": table},
        ).scalar()
    )


@pytest.mark.parametrize("module,table", TABLES)
def test_the_model_declares_a_set_null_project_link(module, table):
    with blank_session() as db:
        schema = _qualified(db, module)
        assert _fk_delete_rule(db, schema, table) == "SET NULL"
        assert _has_index(db, schema, table)


def test_the_revision_chains_with_a_short_id():
    module = _load()
    assert module.revision == "fp_0001_form_project_links"
    assert len(module.revision) <= 32
    assert module.down_revision


def test_upgrade_adds_the_links_idempotently_and_downgrade_removes_them():
    module = _load()
    with blank_session() as db:
        for mod, table in TABLES:
            db.execute(text(f"alter table {_qualified(db, mod)}.{table} drop column project_id"))
        context = MigrationContext.configure(db.connection())
        with Operations.context(context):
            module.upgrade()
            module.upgrade()
        for mod, table in TABLES:
            schema = _qualified(db, mod)
            assert _fk_delete_rule(db, schema, table) == "SET NULL", table
            assert _has_index(db, schema, table), table
        with Operations.context(context):
            module.downgrade()
        for mod, table in TABLES:
            schema = _qualified(db, mod)
            assert _fk_delete_rule(db, schema, table) is None, table
