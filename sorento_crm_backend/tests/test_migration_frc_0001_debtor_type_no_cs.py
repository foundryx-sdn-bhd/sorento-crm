"""RED tests - FORM-ROUTING-CONDITIONS migration `frc_0001_debtor_type_no_cs`
(DT-1, DT-2, PC-1, NC-1).

The migration module is loaded by path (as tests/test_migration_sdn_0002_so_debtor_backfill.py
does). The backfill is a module-level function `backfill_debtor_type(connection) -> int`
(rows changed) so it can be run against the blank schema, which is built from the models and
therefore already has the column; the DDL itself is asserted against the model-built schema
(column, CHECK, FK ON DELETE SET NULL, nullable cs_pic_user_id).

A `test_migration_*` file: CI runs it serially (LESSONS 97).
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.models.base import set_company_scope
from app.models.company import Company
from app.models.order import Customer
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from tests._pg_fixture import blank_session

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "frc_0001_debtor_type_no_cs.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("zzt_migration_frc_0001", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db():
    register_company_scope_listeners()
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


def _sfx() -> str:
    return uuid.uuid4().hex[:6].upper()


def _pre_migration_customer(db, code: str, name: str, *, company_id=DEFAULT_COMPANY_ID) -> str:
    """A customer as it stood before the release: debtor_type NULL (set by raw SQL because the
    insert hook would fill it)."""
    row = Customer(customer_code=code, customer_name=name, company_id=company_id)
    db.add(row)
    db.flush()
    db.execute(text("UPDATE customers SET debtor_type = NULL WHERE id = :i"), {"i": str(row.id)})
    return str(row.id)


def _dt(db, cid):
    return db.execute(text("SELECT debtor_type FROM customers WHERE id = :i"), {"i": cid}).scalar_one()


def test_revision_ids_chain_onto_the_current_head():
    mod = _migration_module()
    assert mod.revision == "frc_0001_debtor_type_no_cs"
    assert mod.down_revision == "sql_0001_set_quote_line"
    assert len(mod.revision) <= 32


def test_dt2_backfill_reads_project_cash_project_and_null(db):
    mocha = Company(id=str(uuid.uuid4()), name="ZZT Mocha", code=f"ZZTMCH{_sfx()}")
    db.add(mocha)
    db.flush()
    ids = {
        "empire": _pre_migration_customer(db, f"302-ZZT{_sfx()}", "ZZT EMPIRE RESOURCES (PROJECT-CASH)"),
        "charles": _pre_migration_customer(db, f"302-ZZT{_sfx()}", "ZZT CHARLES RENO"),
        "global": _pre_migration_customer(db, f"300-ZZT{_sfx()}", "ZZT GLOBAL INGRESS SDN BHD (PROJECT)"),
        "walkin": _pre_migration_customer(db, f"301-ZZT{_sfx()}", "CASH (PERSON A)"),
        "tagged": _pre_migration_customer(db, f"301-ZZT{_sfx()}", "PERSON B (PROJECT-CASH)"),
        "mocha": _pre_migration_customer(db, f"300-ZZT{_sfx()}", "ZZT MOCHA ACCOUNT", company_id=mocha.id),
    }

    _migration_module().backfill_debtor_type(db.connection())

    assert {k: _dt(db, v) for k, v in ids.items()} == {
        "empire": "project_cash",
        "charles": "project_cash",
        "global": "project",
        "walkin": None,
        "tagged": "project",
        "mocha": "project",
    }


def test_backfill_agrees_with_the_python_rule(db):
    from app.services.debtor_type import default_debtor_type

    rows = [
        (f"302-ZZT{_sfx()}", "ZZT PLAIN"),
        (f"301-ZZT{_sfx()}", "CASH SALES - CUSTOMER A"),
        (f"301-ZZT{_sfx()}", "CASH - CUSTOMER A"),
        (f"301-ZZT{_sfx()}", "CASHEW TRADING"),
        (f"300-ZZT{_sfx()}", "ZZT OTHER SDN BHD"),
    ]
    ids = [(_pre_migration_customer(db, c, n), c, n) for c, n in rows]

    _migration_module().backfill_debtor_type(db.connection())

    for cid, code, name in ids:
        assert _dt(db, cid) == default_debtor_type(code, name), (code, name)


def test_backfill_never_overwrites_a_set_value_and_rerun_changes_nothing(db):
    kept = _pre_migration_customer(db, f"302-ZZT{_sfx()}", "ZZT HAND SET")
    db.execute(text("UPDATE customers SET debtor_type = 'cash_sales' WHERE id = :i"), {"i": kept})
    _pre_migration_customer(db, f"300-ZZT{_sfx()}", "ZZT FILL ME")
    mod = _migration_module()

    assert mod.backfill_debtor_type(db.connection()) >= 1
    assert _dt(db, kept) == "cash_sales"
    assert mod.backfill_debtor_type(db.connection()) == 0


# ------------------------------------------------------------------ schema (model-built blank schema)


def test_dt1_check_allows_only_the_three_values_or_null(db):
    cid = _pre_migration_customer(db, f"300-ZZT{_sfx()}", "ZZT CHECK")
    for good in ("project", "project_cash", "cash_sales", None):
        db.execute(text("UPDATE customers SET debtor_type = :v WHERE id = :i"), {"v": good, "i": cid})
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(text("UPDATE customers SET debtor_type = 'wholesale' WHERE id = :i"), {"i": cid})


def test_pc1_purchase_requests_customer_id_fk_set_null_and_indexed(db):
    insp = inspect(db.connection())
    cols = {c["name"]: c for c in insp.get_columns("purchase_requests")}
    assert "customer_id" in cols and cols["customer_id"]["nullable"] is True
    fks = [f for f in insp.get_foreign_keys("purchase_requests") if f["constrained_columns"] == ["customer_id"]]
    assert fks and fks[0]["referred_table"] == "customers"
    assert (fks[0].get("options") or {}).get("ondelete", "").upper() == "SET NULL"
    assert any(ix["column_names"] == ["customer_id"] for ix in insp.get_indexes("purchase_requests"))


def test_nc1_cs_pic_user_id_is_nullable(db):
    cols = {c["name"]: c for c in inspect(db.connection()).get_columns("respond_contact_cs_routing")}
    assert cols["cs_pic_user_id"]["nullable"] is True
