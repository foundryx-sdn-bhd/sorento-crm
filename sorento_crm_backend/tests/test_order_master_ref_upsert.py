"""Order master-ref upsert helpers - the logic the import path now reuses.

`import_excel_tracking` populates `customer_id` / `transporter_id` by routing each
mapped row through `_sync_order_master_refs`, exactly like `create_order` /
`update_order`. These tests pin that helper's find-or-create + idempotency +
pair-match + FK re-point behavior against a real Postgres session.
"""
from __future__ import annotations

import pytest

from app.models.order import Customer, Transporter
from app.services.order_service import OrderService
from tests._pg_fixture import blank_session


@pytest.fixture()
def db():
    """A blank Postgres schema, rolled back after the test.

    Was in-memory sqlite with a hand-registered `btrim` shim, because the upsert
    helpers key on `func.btrim(...)` -- a Postgres builtin sqlite lacks. On the
    real engine that is just the real function.
    """
    with blank_session() as session:
        yield session


def _svc(db):
    return OrderService(db)


def test_customer_created_from_new_debtor(db):
    svc = _svc(db)
    cid = svc._upsert_customer_from_debtor("Deluxe Home Center (KTN)", "300-D093")
    db.flush()
    row = db.query(Customer).filter(Customer.id == cid).one()
    assert row.customer_name == "Deluxe Home Center (KTN)"
    assert row.customer_code == "300-D093"
    assert db.query(Customer).count() == 1


def test_customer_upsert_is_idempotent(db):
    svc = _svc(db)
    a = svc._upsert_customer_from_debtor("Acme Sdn Bhd", "300-A001")
    b = svc._upsert_customer_from_debtor("  acme sdn bhd ", "300-a001")  # case/space noise
    assert a == b
    assert db.query(Customer).count() == 1


def test_same_code_distinct_names_share_one_row(db):
    """DEBTOR-CODE-UNIQUE (owner, 4 Oct 2026): one customer per debtor code. A second name
    under the same code links to the existing row and never renames it; the order keeps
    its own `debtor_name`."""
    svc = _svc(db)
    a = svc._upsert_customer_from_debtor("Deluxe Home Center (KTN)", "300-D093")
    b = svc._upsert_customer_from_debtor("Deluxe Home Center AC (I)", "300-D093")
    assert a == b
    assert db.query(Customer).count() == 1
    assert db.query(Customer).one().customer_name == "Deluxe Home Center (KTN)"


def test_blank_code_makes_no_customer(db):
    """DBR-DEBTOR-CLEANUP: no `DBR-<hash>` slug any more; a blank code has no customer."""
    svc = _svc(db)
    assert svc._upsert_customer_from_debtor("No Code Debtor", None) is None
    assert svc._upsert_customer_from_debtor("No Code Debtor", "") is None
    assert db.query(Customer).count() == 0


def test_transporter_created_and_idempotent(db):
    svc = _svc(db)
    a = svc._upsert_transporter_from_text("Sri Jaya Logistics")
    b = svc._upsert_transporter_from_text("  sri jaya logistics ")
    assert a == b
    row = db.query(Transporter).filter(Transporter.id == a).one()
    assert row.name == "Sri Jaya Logistics"
    assert row.normalized_name == "sri jaya logistics"
    assert db.query(Transporter).count() == 1


def test_sync_populates_both_fks_from_text(db):
    """The single call the import branches make: debtor + transporter -> FKs."""
    svc = _svc(db)
    mapped = {
        "order_number": "DO-1",
        "debtor_name": "Fresh Debtor",
        "debtor_code": "300-F001",
        "transporter": "Fresh Transporter",
    }
    out = svc._sync_order_master_refs(mapped)
    db.flush()
    assert out["customer_id"] == str(db.query(Customer).one().id)
    assert out["transporter_id"] == str(db.query(Transporter).one().id)


def test_sync_ignores_blank_text(db):
    svc = _svc(db)
    out = svc._sync_order_master_refs({"order_number": "DO-2", "debtor_name": "", "transporter": None})
    assert "customer_id" not in out
    assert "transporter_id" not in out
    assert db.query(Customer).count() == 0
    assert db.query(Transporter).count() == 0
