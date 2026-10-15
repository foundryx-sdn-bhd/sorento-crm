"""Migration `r2g_0001_series_sets_uuid_id` (RELEASE2-GUARDS): projects.series_sets gets a uuid id.

The table from sql_0001_set_quote_line has a composite (series_id, product_set_id) key. The
migration adds `id`, keeps the pair unique through an index and moves the primary key onto
`id`, idempotently (the shared dev DB converges through create_all).
Named `test_migration_*.py`: CI runs it in the serial migration pass.

The migration names `projects.series_sets` outright, so its statements run here with the
schema swapped for the blank session's scratch projects schema. Calling `upgrade()` itself
would reach the real `projects` schema of the shared database.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests._pg_fixture import blank_session

VERSIONS = (Path(__file__).resolve().parent / ".." / "alembic" / "versions").resolve()
REVISION = "r2g_0001_series_sets_uuid_id"
FILE = VERSIONS / f"{REVISION}.py"


def _load():
    assert FILE.exists(), f"missing migration file {FILE}"
    alembic_dir = str(VERSIONS.parent)
    if alembic_dir not in sys.path:
        sys.path.insert(0, alembic_dir)
    spec = importlib.util.spec_from_file_location(f"m_{REVISION}", FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scratch_projects(db) -> str:
    return f'"{db.execute(text("SELECT current_schema()")).scalar()}_projects"'


def _run(db, statements, schema: str) -> None:
    for statement in statements:
        local = statement.replace("projects.", f"{schema}.")
        assert not re.search(r'(?<![\w"])projects\.', local), local  # never the real schema
        db.execute(text(local))


def _pk_columns(db, schema: str) -> list[str]:
    return list(
        db.execute(
            text(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = CAST(:t AS regclass) AND i.indisprimary ORDER BY a.attname"
            ),
            {"t": f"{schema}.series_sets"},
        ).scalars()
    )


def _has_id_column(db) -> bool:
    return db.execute(
        text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = current_schema() || '_projects' "
            "AND table_name = 'series_sets' AND column_name = 'id'"
        )
    ).scalar() == 1


def _has_pair_index(db) -> bool:
    return db.execute(
        text(
            "SELECT count(*) FROM pg_indexes WHERE schemaname = current_schema() || '_projects' "
            "AND indexname = 'uq_project_series_sets_pair'"
        )
    ).scalar() == 1


def test_revision_id_and_parent():
    module = _load()
    assert module.revision == REVISION
    assert len(module.revision) <= 32
    assert module.down_revision == "wc_compat_0002_kind"


def test_upgrade_twice_then_downgrade():
    from tests.test_set_quote_line import _env, _series, _set

    module = _load()
    with blank_session() as db:
        schema = _scratch_projects(db)
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        series = _series(db, env["company_id"])

        # Back to the sql_0001 shape: no id, composite primary key.
        _run(db, module.DOWNGRADE_SQL, schema)
        assert _pk_columns(db, schema) == ["product_set_id", "series_id"]
        assert not _has_id_column(db)
        db.execute(
            text("INSERT INTO series_sets (series_id, product_set_id, selling_price) VALUES (:s, :p, 100)"),
            {"s": series.id, "p": product_set.id},
        )

        _run(db, module.UPGRADE_SQL, schema)
        _run(db, module.UPGRADE_SQL, schema)  # idempotent

        assert _pk_columns(db, schema) == ["id"]
        assert _has_pair_index(db)
        assert db.execute(text("SELECT count(*) FROM series_sets WHERE id IS NULL")).scalar() == 0
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(
                    text("INSERT INTO series_sets (series_id, product_set_id) VALUES (:s, :p)"),
                    {"s": series.id, "p": product_set.id},
                )

        _run(db, module.DOWNGRADE_SQL, schema)
        assert _pk_columns(db, schema) == ["product_set_id", "series_id"]
        assert not _has_id_column(db)
        assert not _has_pair_index(db)
