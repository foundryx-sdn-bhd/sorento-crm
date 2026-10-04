"""DEBTOR-CODE-LINK: backfill `sales_orders.debtor_name` from the customer's CURRENT name.

`sdn_0001_so_debtor_name` added the column (owner, 30 Sep 2026: the name an SO was issued under
is per document) but left every existing order NULL, so the SO screens fell back to the live
master name and a renamed customer rewrote the name on its old orders. This data migration
freezes today's master name onto every order that carries none: additive, never overwrites a
stored name, idempotent, and a no-op downgrade.

A `test_migration_*` file: CI runs it serially (LESSONS 97).
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.order import Customer, SalesOrder
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from tests._pg_fixture import blank_session, unique_code

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "sdn_0002_so_debtor_backfill.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("zzt_migration_sdn_0002", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db():
    register_company_scope_listeners()
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


def _customer(db, name: str) -> Customer:
    row = Customer(customer_code=unique_code("ZZTSDN"), customer_name=name, company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def _so(db, *, customer_id=None, debtor_name=None) -> SalesOrder:
    so = SalesOrder(
        so_number=unique_code("SO"),
        status="open",
        customer_id=customer_id,
        debtor_name=debtor_name,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(so)
    db.flush()
    return so


def _name(db, so_id) -> str | None:
    return db.execute(
        text("SELECT debtor_name FROM sales_orders WHERE id = :i"), {"i": str(so_id)}
    ).scalar()


def test_a_renamed_customer_leaves_its_existing_order_on_the_old_name(db):
    customer = _customer(db, "NAME-OLD SDN BHD")
    so = _so(db, customer_id=str(customer.id))

    _migration_module().backfill_so_debtor_name(db.connection())
    db.execute(
        text("UPDATE customers SET customer_name = 'NAME-NEW SDN BHD' WHERE id = :i"),
        {"i": str(customer.id)},
    )

    assert _name(db, so.id) == "NAME-OLD SDN BHD"


def test_a_stored_order_name_is_never_overwritten(db):
    customer = _customer(db, "NAME-MASTER")
    so = _so(db, customer_id=str(customer.id), debtor_name="NAME-ON-THE-SO")

    _migration_module().backfill_so_debtor_name(db.connection())

    assert _name(db, so.id) == "NAME-ON-THE-SO"


def test_an_order_without_a_customer_stays_null(db):
    so = _so(db, customer_id=None)

    _migration_module().backfill_so_debtor_name(db.connection())

    assert _name(db, so.id) is None


def test_the_master_name_is_trimmed_like_the_ingest_writes_it(db):
    customer = _customer(db, "  NAME-PADDED  ")
    so = _so(db, customer_id=str(customer.id))

    _migration_module().backfill_so_debtor_name(db.connection())

    assert _name(db, so.id) == "NAME-PADDED"


def test_a_second_run_changes_nothing(db):
    customer = _customer(db, "NAME-A")
    _so(db, customer_id=str(customer.id))
    mod = _migration_module()

    assert mod.backfill_so_debtor_name(db.connection()) >= 1
    assert mod.backfill_so_debtor_name(db.connection()) == 0


def test_upgrade_runs_the_backfill_and_downgrade_is_a_no_op(db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    customer = _customer(db, "NAME-UP")
    so = _so(db, customer_id=str(customer.id))
    mod = _migration_module()

    context = MigrationContext.configure(connection=db.connection())
    with Operations.context(context):
        mod.upgrade()
        assert _name(db, so.id) == "NAME-UP"
        mod.downgrade()
    assert _name(db, so.id) == "NAME-UP"
    assert len(mod.revision) <= 32


def test_an_order_pointing_at_another_companys_customer_stays_null(db):
    """Security nit: the backfill never copies a name across companies."""
    from app.models.company import Company

    other = Company(id=str(uuid.uuid4()), name="ZZTSDN B", code=unique_code("ZZTSDN")[:10])
    db.add(other)
    db.flush()
    foreign = Customer(customer_code=unique_code("ZZTSDN"), customer_name="NAME-FOREIGN", company_id=str(other.id))
    db.add(foreign)
    db.flush()
    so = _so(db, customer_id=str(foreign.id))

    _migration_module().backfill_so_debtor_name(db.connection())

    assert _name(db, so.id) is None
