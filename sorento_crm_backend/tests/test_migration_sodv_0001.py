"""SO-DELIVERIES-VIEW migration sodv_0001 (AC-SD12): index on order_lines (company_id, from_doc_no).

Modelled on the precedent in tests/test_migration_scope_seeds_debtor_company.py: the module is
loaded by path, the index build helpers (`_index_valid`, `_ensure_index`, as in
alembic/versions/mig_idx_chunks_document_id.py) run on a scratch schema, and the CONCURRENTLY /
lock_timeout shape is pinned from the source because an autocommit block cannot run inside the
scratch transaction.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.models.order import OrderLine
from tests._pg_fixture import blank_session

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "sodv_0001_ol_from_doc_idx.py"
INDEX = "ix_order_lines_company_from_doc_no"


def _load():
    spec = importlib.util.spec_from_file_location("m_sodv_0001", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _state(db):
    row = db.execute(
        text(
            "SELECT i.indisvalid, pg_get_indexdef(i.indexrelid) FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE i.indrelid = 'order_lines'::regclass AND c.relname = :n"
        ),
        {"n": INDEX},
    ).first()
    return tuple(row) if row else None


def test_the_model_declares_the_index_so_create_all_databases_get_it():
    declared = {ix.name: [c.name for c in ix.columns] for ix in OrderLine.__table__.indexes}
    assert INDEX in declared
    assert declared[INDEX] == ["company_id", "from_doc_no"]


def test_revision_ids_are_within_32_chars_and_chain_onto_main():
    module = _load()
    assert module.revision == "sodv_0001_ol_from_doc_idx"
    assert len(module.revision) <= 32
    assert module.down_revision


def test_the_migration_chain_has_a_single_head_that_is_ours():
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["sodv_0001_ol_from_doc_idx"]


def test_upgrade_builds_concurrently_under_a_bounded_lock_wait_and_downgrade_drops_it():
    src = MIGRATION.read_text()
    assert INDEX in src
    assert "CONCURRENTLY" in src
    assert "lock_timeout" in src
    assert "autocommit_block" in src
    assert "order_lines" in src and "company_id" in src and "from_doc_no" in src
    assert "DROP INDEX" in src.split("def downgrade", 1)[1]


def test_building_the_index_makes_it_valid_over_company_and_from_doc_no(db):
    module = _load()
    db.execute(text(f"DROP INDEX IF EXISTS {INDEX}"))
    assert _state(db) is None

    module._ensure_index(db.connection(), concurrently=False)

    valid, definition = _state(db)
    assert valid is True
    assert "company_id" in definition and "from_doc_no" in definition


def test_running_the_build_again_is_a_no_op(db):
    module = _load()
    db.execute(text(f"DROP INDEX IF EXISTS {INDEX}"))
    module._ensure_index(db.connection(), concurrently=False)
    first = _state(db)

    module._ensure_index(db.connection(), concurrently=False)

    assert _state(db) == first
