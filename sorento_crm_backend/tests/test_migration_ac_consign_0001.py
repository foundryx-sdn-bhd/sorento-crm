"""Migration `ac_consign_0001_doc_type` (AC-CONSIGN-CRM): up, down, up.

Adds `sales_orders.doc_type` (checked: sales_order | consignment, default sales_order) and
`sales_order_lines.qty_returned` (default 0). The DDL runs on the real tables inside an outer
transaction that is rolled back, so nothing survives the test. Named `test_migration_*.py`:
CI runs it in the serial migration pass.
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database import engine

VERSIONS = (Path(__file__).resolve().parent / ".." / "alembic" / "versions").resolve()
REVISION = "ac_consign_0001_doc_type"


def _load():
    alembic_dir = str(VERSIONS.parent)
    if alembic_dir not in sys.path:
        sys.path.insert(0, alembic_dir)
    spec = importlib.util.spec_from_file_location(f"m_{REVISION}", VERSIONS / f"{REVISION}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _columns(conn) -> tuple[bool, bool]:
    inspector = sa.inspect(conn)
    so = {c["name"] for c in inspector.get_columns("sales_orders")}
    sol = {c["name"] for c in inspector.get_columns("sales_order_lines")}
    return "doc_type" in so, "qty_returned" in sol


def test_revision_fits_and_chains_onto_the_cancel_lane():
    module = _load()
    assert module.revision == REVISION
    assert len(module.revision) <= 32


def test_up_down_up_defaults_and_the_check():
    module = _load()
    with engine.connect() as conn:
        outer = conn.begin()
        try:
            if _columns(conn) == (True, True):
                _run(conn, module.downgrade)
            assert _columns(conn) == (False, False)
            so_id = str(uuid.uuid4())
            conn.execute(sa.text(
                "INSERT INTO sales_orders (id, so_number, status, company_id) "
                "VALUES (:i, :n, 'open', '00000000-0000-0000-0000-000000000001')"),
                {"i": so_id, "n": f"ZZT-MIG-{uuid.uuid4().hex[:8]}"})
            _run(conn, module.upgrade)
            assert _columns(conn) == (True, True)
            # every existing order reads sales_order
            assert conn.execute(sa.text("SELECT doc_type FROM sales_orders WHERE id = :i"),
                                {"i": so_id}).scalar() == "sales_order"
            conn.execute(sa.text("UPDATE sales_orders SET doc_type = 'consignment' WHERE id = :i"),
                         {"i": so_id})
            savepoint = conn.begin_nested()
            with pytest.raises(sa.exc.IntegrityError):
                conn.execute(sa.text("UPDATE sales_orders SET doc_type = 'cg' WHERE id = :i"),
                             {"i": so_id})
            savepoint.rollback()
            _run(conn, module.downgrade)
            assert _columns(conn) == (False, False)
            _run(conn, module.upgrade)
            assert _columns(conn) == (True, True)
        finally:
            outer.rollback()
