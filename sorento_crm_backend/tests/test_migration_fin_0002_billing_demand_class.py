"""Migration `fin_0002_billing_demand_class` (finance S1, #1309): up, down, up.

Plan 3.4 (round 3, ruling Q14): a billing document's sales order type is stored, so the
migration adds `finance.billing_documents.demand_class` with the closed vocabulary's own
CHECK (`demand_class.check_constraint_sql`, the one `sales_agents` uses), and republishes
the chatbot parser prompt, which now teaches `sales_basis` "invoiced" (UAC S1-6), moving the
`production` label onto it the `sales_s1_reports_module` way (owner ruling 21 Sep 2026: the
deploy ships the config).

The column half runs `fin_0001` then `fin_0002` on a scratch schema through
`schema_translate_map`, as `test_migration_fin_0001_billing_documents.py` does, inside an
outer transaction that is rolled back. The prompt half runs in `blank_session`, as
`test_migration_sales_s1_module.py` does.

Named `test_migration_*.py`: its DDL touches the shared tables `fin_0001` references, so CI
runs it in the serial migration pass.
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


def test_revision_id_fits_and_chains_onto_fin_0001():
    module = _load("fin_0002_billing_demand_class")
    assert module.revision == "fin_0002_billing_demand_class"
    assert len(module.revision) <= 32
    assert module.down_revision == "fin_0001_billing_documents"


@pytest.fixture()
def migrated(monkeypatch):
    """(raw connection, scratch schema, fin_0002 module), both revisions applied."""
    first = _load("fin_0001_billing_documents")
    second = _load("fin_0002_billing_demand_class")
    # The prompt half has its own test; here the column is the subject.
    monkeypatch.setattr(second, "republish_parser", lambda bind: None)
    monkeypatch.setattr(second, "restore_parser_label", lambda bind: None)
    scratch = f"zzs_mig_fin2_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with engine.connect() as raw:
        outer = raw.begin()
        try:
            raw.exec_driver_sql(f'CREATE SCHEMA "{scratch}"')
            # `None: None` keeps the default schema as is but gives every statement compiled here
            # its None placeholder: the blank-schema engine shares this engine's compiled cache,
            # and a cached statement compiled without one breaks a later blank_session test.
            conn = raw.execution_options(schema_translate_map={None: None, "finance": scratch})
            _run(conn, first.upgrade)
            _run(conn, second.upgrade)
            yield raw, conn, scratch, second
        finally:
            outer.rollback()


def _company(raw) -> str:
    company_id = str(uuid.uuid4())
    raw.execute(
        sa.text("INSERT INTO companies (id, name, code) VALUES (:id, 'ZZFIN2 mig', :code)"),
        {"id": company_id, "code": f"ZN{uuid.uuid4().hex[:6]}"},
    )
    return company_id


def _insert(raw, scratch, company_id, demand_class):
    raw.execute(
        sa.text(
            f'INSERT INTO "{scratch}".billing_documents '
            "(id, company_id, document_type, doc_no, doc_date, source_ref, demand_class) "
            "VALUES (:id, :c, 'invoice', 'N', '2026-01-01', :ref, :cls)"
        ),
        {"id": str(uuid.uuid4()), "c": company_id, "ref": uuid.uuid4().hex, "cls": demand_class},
    )


def test_the_column_is_nullable_and_holds_the_closed_vocabulary_only(migrated):
    raw, _conn, scratch, _module = migrated
    columns = {c["name"]: c for c in sa.inspect(raw).get_columns("billing_documents", schema=scratch)}
    assert columns["demand_class"]["nullable"] is True
    company_id = _company(raw)
    for value in ("retail", "project", None):
        _insert(raw, scratch, company_id, value)
    savepoint = raw.begin_nested()
    with pytest.raises(sa.exc.IntegrityError):
        _insert(raw, scratch, company_id, "dealer")
    savepoint.rollback()


def test_down_then_up_again(migrated):
    raw, conn, scratch, module = migrated
    _run(conn, module.downgrade)
    columns = {c["name"] for c in sa.inspect(raw).get_columns("billing_documents", schema=scratch)}
    assert "demand_class" not in columns
    _run(conn, module.upgrade)
    columns = {c["name"] for c in sa.inspect(raw).get_columns("billing_documents", schema=scratch)}
    assert "demand_class" in columns


def test_downgrade_restores_the_parser_label(migrated, monkeypatch):
    raw, conn, _scratch, module = migrated
    calls = []
    monkeypatch.setattr(module, "restore_parser_label", lambda bind: calls.append(bind))
    _run(conn, module.downgrade)
    assert len(calls) == 1


def test_the_model_declares_the_same_check_for_a_create_all_database():
    from app.models.finance import BillingDocument

    checks = {
        c.name: str(c.sqltext)
        for c in BillingDocument.__table__.constraints
        if isinstance(c, sa.CheckConstraint)
    }
    from app.services.scm.demand_class import check_constraint_sql

    assert checks["ck_finance_billing_documents_demand_class"] == check_constraint_sql()


# ------------------------------------------------------------------ the prompt
def _production_template(db) -> str:
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

    label = (
        db.query(AIPromptLabel)
        .filter_by(name="chatbot_semantic_parser", label="production")
        .one()
    )
    return db.query(AIPromptVersion).filter_by(id=label.version_id).one().template, label.version_id


def test_the_parser_prompt_that_says_invoiced_is_production_after_upgrade_and_not_after_down():
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from app.services.ai_prompt_seed import seed_prompt_registry
    from tests._pg_fixture import blank_session
    from tests.test_migration_sales_s1_module import _seed_roles_and_order_domain

    module = _load("fin_0002_billing_demand_class")
    with blank_session() as db:
        _seed_roles_and_order_domain(db)
        seed_prompt_registry(db.connection())
        # Production as a deploy finds it: a version from before this revision's words.
        versions = [v for (v,) in db.query(AIPromptVersion.version).filter_by(
            name="chatbot_semantic_parser")]
        old = AIPromptVersion(
            name="chatbot_semantic_parser",
            version=max(versions, default=0) + 1,
            type="text",
            template="the parser before finance S1",
            variables=[],
            config_json={},
        )
        db.add(old)
        db.flush()
        label = db.query(AIPromptLabel).filter_by(
            name="chatbot_semantic_parser", label="production").one()
        label.version_id = old.id
        db.flush()

        module.republish_parser(db.connection())
        db.expire_all()
        template, promoted_id = _production_template(db)
        assert promoted_id != old.id
        assert '"invoiced", "invoices", "billed" -> "invoiced"' in template

        module.republish_parser(db.connection())  # idempotent: the label stays put
        db.expire_all()
        assert _production_template(db)[1] == promoted_id

        module.restore_parser_label(db.connection())
        db.expire_all()
        assert _production_template(db)[1] == old.id

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
pytestmark = pytest.mark.parser_prompt_rows
