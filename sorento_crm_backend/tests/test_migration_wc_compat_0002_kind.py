"""Migration `wc_compat_0002_kind` (MIRROR-LINKS): product_compatibility.kind.

Plan section 5: idempotent SQL (the shared dev DB converges through create_all), a checked
`kind` column defaulting to seat_cover, and a unique index over (accessory, host, kind).
Named `test_migration_*.py`: CI runs it in the serial migration pass.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests._pg_fixture import blank_session

VERSIONS = (Path(__file__).resolve().parent / ".." / "alembic" / "versions").resolve()
REVISION = "wc_compat_0002_kind"
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


def _run(db, fn):
    with Operations.context(MigrationContext.configure(db.connection())):
        fn()


def test_revision_id_and_parent():
    module = _load()
    assert module.revision == REVISION
    assert len(module.revision) <= 32
    assert module.down_revision == "frc_0001_debtor_type_no_cs"


def test_upgrade_sql_is_idempotent_by_construction():
    source = FILE.read_text()
    assert "ADD COLUMN IF NOT EXISTS kind" in source
    assert "DEFAULT 'seat_cover'" in source
    assert "ck_product_compatibility_kind" in source
    assert "pg_constraint" in source  # the guarded DO block
    assert "DROP INDEX IF EXISTS uq_product_compatibility_pair" in source
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_product_compatibility_pair_kind" in source


def _kind_column(db):
    return db.execute(
        text(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'product_compatibility' "
            "AND column_name = 'kind'"
        )
    ).one_or_none()


def _index_names(db) -> set[str]:
    return {
        r[0]
        for r in db.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename = 'product_compatibility'"
            )
        )
    }


def test_upgrade_twice_then_downgrade():
    module = _load()
    with blank_session() as db:
        for stmt in (
            "DROP INDEX IF EXISTS uq_product_compatibility_pair_kind",
            "ALTER TABLE product_compatibility DROP CONSTRAINT IF EXISTS ck_product_compatibility_kind",
            "ALTER TABLE product_compatibility DROP COLUMN IF EXISTS kind",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_product_compatibility_pair "
            "ON product_compatibility (seat_cover_product_id, wc_product_id)",
        ):
            db.execute(text(stmt))
        # A link written before the migration is a seat cover link afterwards.
        from tests._mc_lookup_seed import product
        from app.services.company_scope import DEFAULT_COMPANY_ID

        acc = product(db, company_id=DEFAULT_COMPANY_ID)
        host = product(db, company_id=DEFAULT_COMPANY_ID)
        db.execute(
            text(
                "INSERT INTO product_compatibility "
                "(id, company_id, seat_cover_product_id, wc_product_id, source) "
                "VALUES (gen_random_uuid(), :c, :a, :h, 'manual')"
            ),
            {"c": DEFAULT_COMPANY_ID, "a": acc.id, "h": host.id},
        )
        _run(db, module.upgrade)
        _run(db, module.upgrade)  # idempotent
        assert db.execute(text("SELECT kind FROM product_compatibility")).scalars().all() == [
            "seat_cover"
        ]
        is_nullable, default = _kind_column(db)
        assert is_nullable == "NO"
        assert "seat_cover" in (default or "")
        names = _index_names(db)
        assert "uq_product_compatibility_pair_kind" in names
        assert "uq_product_compatibility_pair" not in names
        assert (
            db.execute(
                text("SELECT count(*) FROM pg_constraint WHERE conname = 'ck_product_compatibility_kind'")
            ).scalar()
            == 1
        )
        _run(db, module.downgrade)
        assert _kind_column(db) is None
        names = _index_names(db)
        assert "uq_product_compatibility_pair" in names
        assert "uq_product_compatibility_pair_kind" not in names
