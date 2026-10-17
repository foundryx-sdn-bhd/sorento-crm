"""Migration `fin_0003_billing_outstanding` (AC-BILLING S5): up, down, up.

Adds `outstanding Numeric(15,2) NULL`, `due_date Date NULL` and `outstanding_as_of timestamp NULL` to
`finance.billing_documents`; NULL means "not synced", never 0. Downgrade drops them. Idempotent:
a second upgrade on a table that already has them does not raise.

Same shape as `test_migration_fin_0002_billing_demand_class.py`: `fin_0001`, `fin_0002` and `fin_0003`
run on a scratch schema through `schema_translate_map`, inside an outer transaction that is rolled
back. `fin_0002`'s parser-prompt half has its own test and is stubbed here.

Named `test_migration_*.py`: its DDL touches the shared tables `fin_0001` references, so CI runs it
in the serial migration pass.
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
COLUMNS = ("outstanding", "due_date", "outstanding_as_of")


def _load(name: str):
    import sys

    alembic_dir = str(VERSIONS.parent)
    if alembic_dir not in sys.path:
        sys.path.insert(0, alembic_dir)
    spec = importlib.util.spec_from_file_location(f"m_{name}", VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _columns(raw, scratch) -> dict:
    return {c["name"]: c for c in sa.inspect(raw).get_columns("billing_documents", schema=scratch)}


def test_revision_id_fits_and_follows_fin_0002_as_the_single_head():
    """fin_0002 is an ancestor and fin_0003 is the only head. The direct parent is whatever
    main's head is when the lane lands (`scripts/alembic-reparent.sh` moves it), so it is not
    pinned here."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    module = _load("fin_0003_billing_outstanding")
    assert module.revision == "fin_0003_billing_outstanding"
    assert len(module.revision) <= 32
    script = ScriptDirectory.from_config(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")))
    assert script.get_heads() == ["fin_0003_billing_outstanding"]
    ancestors = {rev.revision for rev in script.walk_revisions("base", "fin_0003_billing_outstanding")}
    assert "fin_0002_billing_demand_class" in ancestors


@pytest.fixture()
def migrated(monkeypatch):
    """(raw connection, translated connection, scratch schema, fin_0003 module), all three applied."""
    first = _load("fin_0001_billing_documents")
    second = _load("fin_0002_billing_demand_class")
    third = _load("fin_0003_billing_outstanding")
    monkeypatch.setattr(second, "republish_parser", lambda bind: None)
    monkeypatch.setattr(second, "restore_parser_label", lambda bind: None)
    scratch = f"zzs_mig_fin3_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with engine.connect() as raw:
        outer = raw.begin()
        try:
            raw.exec_driver_sql(f'CREATE SCHEMA "{scratch}"')
            # `None: None` keeps the default schema as is; see test_migration_fin_0002 for why.
            conn = raw.execution_options(schema_translate_map={None: None, "finance": scratch})
            _run(conn, first.upgrade)
            _run(conn, second.upgrade)
            _run(conn, third.upgrade)
            yield raw, conn, scratch, third
        finally:
            outer.rollback()


def _company(raw) -> str:
    company_id = str(uuid.uuid4())
    raw.execute(
        sa.text("INSERT INTO companies (id, name, code) VALUES (:id, 'ZZFIN3 mig', :code)"),
        {"id": company_id, "code": f"ZO{uuid.uuid4().hex[:6]}"},
    )
    return company_id


def test_upgrade_adds_the_three_nullable_columns_with_their_types(migrated):
    raw, _conn, scratch, _module = migrated
    columns = _columns(raw, scratch)
    for name in COLUMNS:
        assert name in columns, name
        assert columns[name]["nullable"] is True, name
    outstanding = columns["outstanding"]["type"]
    assert isinstance(outstanding, sa.Numeric)
    assert (outstanding.precision, outstanding.scale) == (15, 2)
    assert isinstance(columns["due_date"]["type"], sa.Date)
    assert isinstance(columns["outstanding_as_of"]["type"], sa.DateTime)
    assert columns["outstanding_as_of"]["type"].timezone is False


def test_existing_rows_stay_null_and_the_columns_hold_values(migrated):
    raw, _conn, scratch, _module = migrated
    company_id = _company(raw)
    doc_id = str(uuid.uuid4())
    raw.execute(
        sa.text(
            f'INSERT INTO "{scratch}".billing_documents '
            "(id, company_id, document_type, doc_no, doc_date, source_ref) "
            "VALUES (:id, :c, 'invoice', 'N', '2026-01-01', :ref)"
        ),
        {"id": doc_id, "c": company_id, "ref": uuid.uuid4().hex},
    )
    row = raw.execute(
        sa.text(
            f'SELECT outstanding, due_date, outstanding_as_of FROM "{scratch}".billing_documents '
            "WHERE id = :id"
        ),
        {"id": doc_id},
    ).one()
    assert tuple(row) == (None, None, None)  # not synced is NULL, never 0
    raw.execute(
        sa.text(
            f'UPDATE "{scratch}".billing_documents SET outstanding = -200.50, '
            "due_date = '2026-11-25', outstanding_as_of = '2026-10-26 09:00:00' WHERE id = :id"
        ),
        {"id": doc_id},
    )
    stored = raw.execute(
        sa.text(f'SELECT outstanding FROM "{scratch}".billing_documents WHERE id = :id'),
        {"id": doc_id},
    ).scalar()
    assert str(stored) == "-200.50"


def test_down_then_up_again(migrated):
    raw, conn, scratch, module = migrated
    _run(conn, module.downgrade)
    after_down = set(_columns(raw, scratch))
    assert not (set(COLUMNS) & after_down)
    assert "demand_class" in after_down  # fin_0002's column is untouched by the downgrade
    _run(conn, module.upgrade)
    assert set(COLUMNS) <= set(_columns(raw, scratch))


def test_upgrade_is_idempotent(migrated):
    raw, conn, scratch, module = migrated
    _run(conn, module.upgrade)  # the columns are already there
    assert set(COLUMNS) <= set(_columns(raw, scratch))


def test_downgrade_is_idempotent(migrated):
    raw, conn, scratch, module = migrated
    _run(conn, module.downgrade)
    _run(conn, module.downgrade)  # the columns are already gone
    assert not (set(COLUMNS) & set(_columns(raw, scratch)))


def test_the_model_declares_the_same_columns_for_a_create_all_database():
    from app.models.finance import BillingDocument

    table = BillingDocument.__table__
    for name in COLUMNS:
        assert name in table.c, name
        assert table.c[name].nullable is True, name
    assert (table.c.outstanding.type.precision, table.c.outstanding.type.scale) == (15, 2)
    assert isinstance(table.c.due_date.type, sa.Date)
    assert isinstance(table.c.outstanding_as_of.type, sa.DateTime)
