"""Migration `fin_0001_billing_documents` (finance S0, #1309): up, down, up.

UAC S0-1 (schema, both tables, NOT NULL company, dormant catalog row), S0-2 (the four slugs,
admin and superadmin grants, the Q16 sweep, idempotent grants, registry, module map), S0-3
(the type CHECK).

The migration's own `upgrade()` and `downgrade()` run through a real alembic `Operations`
context. Its `finance.*` DDL is redirected onto a scratch schema by `schema_translate_map`
(lesson 114: a rolled-back transaction is not isolation for DDL on the shared relations), and
everything else it writes (permission rows, grants, the catalog row) sits in the outer
transaction, which is rolled back. Same shape as `test_migration_sales_0001_teams.py`.

Named `test_migration_*.py` on purpose: its DDL adds foreign keys to the SHARED `companies`,
`customers`, `sales_agents`, `products` and `sales_order_lines`, so it deadlocks with another
worker's migration test inside the xdist pool (measured: DeadlockDetected on `companies` and
`sales_agents` against `test_migration_sales_0001_teams.py`). CI runs that glob serially.
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

MIGRATION = (
    Path(__file__).resolve().parent
    / ".."
    / "alembic"
    / "versions"
    / "fin_0001_billing_documents.py"
).resolve()

SLUGS = {
    "finance.billing_documents.view",
    "finance.billing_documents.export",
    "finance.billing_documents.edit",
    "finance.billing_documents.delete",
}


def _load():
    spec = importlib.util.spec_from_file_location("m_fin_0001", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        fn()


def _role(raw, slug: str) -> str:
    found = raw.execute(sa.text("SELECT id FROM user_roles WHERE slug = :s"), {"s": slug}).scalar()
    if found:
        return str(found)
    role_id = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO user_roles (id, slug, name, description, is_protected, is_default, is_trashed) "
            "VALUES (:id, :s, :s, '', false, false, false)"
        ),
        {"id": role_id, "s": slug},
    )
    return role_id


def _permission(raw, slug: str) -> str:
    found = raw.execute(
        sa.text("SELECT id FROM user_permissions WHERE slug = :s"), {"s": slug}
    ).scalar()
    if found:
        return str(found)
    perm_id = str(uuid.uuid4())
    raw.execute(
        sa.text(
            "INSERT INTO user_permissions (id, slug, name, description, created_at) "
            "VALUES (:id, :s, :s, '', now())"
        ),
        {"id": perm_id, "s": slug},
    )
    return perm_id


def _grant(raw, role_id: str, perm_id: str) -> None:
    raw.execute(
        sa.text(
            "INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at) "
            "VALUES (gen_random_uuid()::text, :r, :p, now()) "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        ),
        {"r": role_id, "p": perm_id},
    )


def _held(raw, role_id: str) -> set[str]:
    rows = raw.execute(
        sa.text(
            "SELECT p.slug FROM user_role_permissions rp "
            "JOIN user_permissions p ON p.id = rp.permission_id "
            "WHERE rp.role_id = :r AND p.slug LIKE 'finance.%'"
        ),
        {"r": role_id},
    )
    return {r[0] for r in rows}


def test_revision_id_fits_alembic_version_and_chains_onto_one_head():
    module = _load()
    assert module.revision == "fin_0001_billing_documents"
    assert len(module.revision) <= 32
    # One parent, never a tuple (the lane brief: chain onto main's single head).
    assert isinstance(module.down_revision, str) and module.down_revision


@pytest.fixture()
def migrated():
    """Yield (raw connection, translated connection, scratch schema, module, roles)."""
    module = _load()
    scratch = f"zzs_mig_fin_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with engine.connect() as raw:
        outer = raw.begin()
        try:
            raw.exec_driver_sql(f'CREATE SCHEMA "{scratch}"')
            # `None: None` keeps the default schema as is but gives every statement compiled here
            # its None placeholder: the blank-schema engine shares this engine's compiled cache,
            # and a cached statement compiled without one breaks a later blank_session test.
            conn = raw.execution_options(schema_translate_map={None: None, "finance": scratch})
            roles = {
                "admin": _role(raw, "admin"),
                "superadmin": _role(raw, "superadmin"),
                "plain": _role(raw, f"zzfin_plain_{uuid.uuid4().hex[:6]}"),
                "feed": _role(raw, f"zzfin_feed_{uuid.uuid4().hex[:6]}"),
            }
            _grant(raw, roles["feed"], _permission(raw, "scm.sales_orders.edit"))
            _run(conn, module.upgrade)
            yield raw, conn, scratch, module, roles
        finally:
            outer.rollback()


def test_upgrade_creates_both_tables_company_scoped(migrated):
    raw, _conn, scratch, _module, _roles = migrated
    insp = sa.inspect(raw)
    assert set(insp.get_table_names(schema=scratch)) == {
        "billing_documents",
        "billing_document_lines",
    }
    for table in ("billing_documents", "billing_document_lines"):
        cols = {c["name"]: c for c in insp.get_columns(table, schema=scratch)}
        assert cols["company_id"]["nullable"] is False, table
        fks = insp.get_foreign_keys(table, schema=scratch)
        assert any(
            fk["referred_table"] == "companies" and fk["constrained_columns"] == ["company_id"]
            for fk in fks
        ), table
    header = {c["name"]: c for c in insp.get_columns("billing_documents", schema=scratch)}
    for column in (
        "document_type",
        "doc_no",
        "doc_date",
        "customer_id",
        "debtor_code",
        "customer_name",
        "sales_agent_id",
        "agent_code",
        "currency_code",
        "currency_rate",
        "net_total",
        "tax_total",
        "total",
        "local_net_total",
        "status",
        "against_document_id",
        "against_doc_no",
        "ref",
        "description",
        "source_system",
        "source_ref",
        "source_modified_at",
        "last_synced_at",
        "created_at",
        "updated_at",
    ):
        assert column in header, column
    assert header["doc_date"]["nullable"] is False
    assert header["source_ref"]["nullable"] is False
    line = {c["name"] for c in insp.get_columns("billing_document_lines", schema=scratch)}
    assert {
        "document_id",
        "line_no",
        "product_id",
        "item_code",
        "quantity",
        "unit_price",
        "discount_amount",
        "net_amount",
        "tax_code",
        "tax_rate",
        "tax_amount",
        "line_total",
        "sales_order_line_id",
        "from_doc_type",
        "from_doc_no",
        "from_line_ref",
        "source_ref",
    } <= line
    indexes = {i["name"]: i for i in insp.get_indexes("billing_documents", schema=scratch)}
    unique = indexes["uq_finance_billing_documents_company_type_ref"]
    assert unique["unique"] and unique["column_names"] == [
        "company_id",
        "document_type",
        "source_ref",
    ]
    assert not indexes["ix_finance_billing_documents_company_type_doc_no"]["unique"]
    assert "ix_finance_billing_documents_company_agent_date" in indexes
    line_indexes = {
        i["name"]: i for i in insp.get_indexes("billing_document_lines", schema=scratch)
    }
    assert line_indexes["uq_finance_billing_document_lines_document_ref"]["unique"]


def test_catalog_row_is_dormant(migrated):
    raw, *_ = migrated
    row = raw.execute(
        sa.text(
            "SELECT is_core, display_name, dependencies FROM app_modules_catalog "
            "WHERE module_key = 'finance'"
        )
    ).one()
    assert row.is_core is False
    assert row.display_name == "Finance"
    assert sorted(row.dependencies) == ["base", "order"]
    assert (
        raw.execute(
            sa.text("SELECT count(*) FROM tenant_modules WHERE module_key = 'finance'")
        ).scalar()
        == 0
    )


def test_slugs_and_grants(migrated):
    raw, _conn, _scratch, module, roles = migrated
    present = {
        r[0]
        for r in raw.execute(
            sa.text("SELECT slug FROM user_permissions WHERE slug LIKE 'finance.%'")
        )
    }
    assert present == SLUGS
    assert _held(raw, roles["admin"]) == SLUGS
    assert _held(raw, roles["superadmin"]) == SLUGS
    assert _held(raw, roles["plain"]) == set()
    # Q16 (a): the feed's role (it holds scm.sales_orders.edit) gets the three the
    # ingest doors need, never export.
    assert _held(raw, roles["feed"]) == {
        "finance.billing_documents.view",
        "finance.billing_documents.edit",
        "finance.billing_documents.delete",
    }

    count = "SELECT count(*) FROM user_role_permissions"
    before = raw.execute(sa.text(count)).scalar()
    module.seed_permissions(raw)
    assert raw.execute(sa.text(count)).scalar() == before


def test_type_check_accepts_the_four_and_rejects_a_fifth(migrated):
    raw, conn, scratch, _module, _roles = migrated
    company_id = str(uuid.uuid4())
    raw.execute(
        sa.text("INSERT INTO companies (id, name, code) VALUES (:id, 'ZZFIN mig', :code)"),
        {"id": company_id, "code": f"ZM{uuid.uuid4().hex[:6]}"},
    )

    def insert(document_type: str, status: str = "posted"):
        raw.execute(
            sa.text(
                f'INSERT INTO "{scratch}".billing_documents '
                "(id, company_id, document_type, doc_no, doc_date, status, source_ref) "
                "VALUES (:id, :c, :t, 'N', '2026-01-01', :s, :ref)"
            ),
            {
                "id": str(uuid.uuid4()),
                "c": company_id,
                "t": document_type,
                "s": status,
                "ref": uuid.uuid4().hex,
            },
        )

    for document_type in ("invoice", "cash_sale", "credit_note", "debit_note"):
        insert(document_type)
    for bad in (("receipt", "posted"), ("invoice", "paid")):
        savepoint = raw.begin_nested()
        with pytest.raises(sa.exc.IntegrityError):
            insert(*bad)
        savepoint.rollback()
    # No date floor (ruling Q2, UAC S0-22): a 2019 document is a valid row.
    raw.execute(
        sa.text(
            f'INSERT INTO "{scratch}".billing_documents '
            "(id, company_id, document_type, doc_no, doc_date, source_ref) "
            "VALUES (:id, :c, 'invoice', 'OLD', '2019-06-30', 'old')"
        ),
        {"id": str(uuid.uuid4()), "c": company_id},
    )


def test_down_then_up_again(migrated):
    raw, conn, scratch, module, _roles = migrated
    _run(conn, module.downgrade)
    insp = sa.inspect(raw)
    assert insp.get_table_names(schema=scratch) == []
    assert (
        raw.execute(
            sa.text("SELECT count(*) FROM app_modules_catalog WHERE module_key = 'finance'")
        ).scalar()
        == 0
    )
    # The permission rows stay (the sales_0001 reasoning: sync_permissions recreates them).
    assert (
        raw.execute(
            sa.text("SELECT count(*) FROM user_permissions WHERE slug LIKE 'finance.%'")
        ).scalar()
        == 4
    )

    _run(conn, module.upgrade)
    insp = sa.inspect(raw)
    assert set(insp.get_table_names(schema=scratch)) == {
        "billing_documents",
        "billing_document_lines",
    }
    assert (
        raw.execute(
            sa.text("SELECT count(*) FROM app_modules_catalog WHERE module_key = 'finance'")
        ).scalar()
        == 1
    )


def test_registry_and_module_map():
    from app.modules.runtime.module_manifest import MODULE_MANIFEST
    from app.modules.runtime.permission_module_map import module_for_permission
    from app.rbac.permission_registry import PERMISSION_REGISTRY

    assert SLUGS <= {entry["slug"] for entry in PERMISSION_REGISTRY}
    for slug in SLUGS:
        assert module_for_permission(slug) == "finance"
    assert "finance" in MODULE_MANIFEST
    assert MODULE_MANIFEST["finance"].dependencies == frozenset({"base", "order"})

    from app.modules.finance.bootstrap import MODULE_KEY

    assert MODULE_KEY == "finance"


def test_sync_permissions_creates_the_four_on_a_create_all_database():
    from app.models.user import UserPermission
    from app.rbac.permission_registry import sync_permissions
    from tests._pg_fixture import pg_empty_schema

    with pg_empty_schema([UserPermission.__table__]) as session:
        sync_permissions(session)
        slugs = {row.slug for row in session.query(UserPermission.slug).all()}
        assert SLUGS <= slugs


def test_models_declare_the_constraints_create_all_needs():
    """A create_all database (CI) must reject what the migrated one rejects."""
    from app.models.finance import BillingDocument, BillingDocumentLine

    header = BillingDocument.__table__
    assert header.schema == "finance"
    assert BillingDocumentLine.__table__.schema == "finance"
    checks = {c.name for c in header.constraints if isinstance(c, sa.CheckConstraint)}
    assert {
        "ck_finance_billing_documents_type",
        "ck_finance_billing_documents_status",
    } <= checks
    assert BillingDocument.__audit_track__ is True
    assert BillingDocument.__audit_entity_type__ == "finance_billing_documents"
    assert not getattr(BillingDocumentLine, "__audit_track__", False)
