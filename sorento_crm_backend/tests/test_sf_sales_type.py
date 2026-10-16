"""SF-SALES-TYPE: a Sponsorship Form keeps a sales type, like a Purchase Request.

UAC: documentation/plans/procurement/sf-sales-type-acceptance-criteria.md.

* ST1/ST2 (pins): the staff create + update routes already store `sales_type` on an SF; only the
  frontend dropped it. These pin that the backend keeps doing so.
* ST3 (pin): the portal write allowlist PR and SF share carries `sales_type`.
* ST5: the SF revision viewer reads `sales_type` with its lookup label, and the printed SF shows a
  "Sales Type:" row right after "Sponsor Subject:" (the Excel document's order).
* ST7: `scripts/backfill_sf_sales_type.py` fills existing SFs from the customer's CURRENT debtor
  type. Dry-run writes nothing; `--apply` touches only customer-linked SFs with no sales type.

Blank Postgres schema, every row seeded here.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.order import Customer
from app.models.procurement import PurchaseRequestHeader
from app.models.user import User
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

BASE = "/api/v1/procurement/purchase-requests"


def _sfx() -> str:
    return uuid.uuid4().hex[:6].upper()


@pytest.fixture(autouse=True)
def _clean():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def db():
    register_company_scope_listeners()
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def _customer(db, code, name, **kw) -> Customer:
    row = Customer(customer_code=code, customer_name=name, company_id=DEFAULT_COMPANY_ID, **kw)
    db.add(row)
    db.flush()
    return row


def _request(db, *, request_type="sponsorship_form", **kw) -> PurchaseRequestHeader:
    fields = dict(
        id=str(uuid.uuid4()),
        request_type=request_type,
        request_number=f"ZZT-{uuid.uuid4().hex[:6]}",
        customer_name="ZZT SPONSOR CUSTOMER SDN BHD",
    )
    fields.update(kw)
    row = PurchaseRequestHeader(**fields)
    db.add(row)
    db.flush()
    return row


def _client(db, monkeypatch) -> TestClient:
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@t.zzt", name="CS PIC X", status="ACTIVE"))
    db.flush()
    principal = {"id": uid, "email": f"{uid}@t.zzt"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, u, slug: True
    )

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _override_scope
    return TestClient(app)


def _stored_sales_type(db, request_id) -> str | None:
    return db.execute(
        text("SELECT sales_type FROM purchase_requests WHERE id = :i"), {"i": str(request_id)}
    ).scalar_one()


# --- ST1 / ST2 / ST3 pins: the write paths keep an SF's sales type ----------- #

def test_staff_create_keeps_the_sf_sales_type(db, monkeypatch):
    cust = _customer(db, f"302-ZZT{_sfx()}", "ZZT CASH CUSTOMER", debtor_type="project_cash")
    body = {
        "request_type": "sponsorship_form",
        "customer_name": cust.customer_name,
        "customer_id": str(cust.id),
        "sales_type": "cash_sales",
        "products": [],
    }

    res = _client(db, monkeypatch).post(f"{BASE}/", json=body)

    assert res.status_code == 201, res.text
    assert res.json()["sales_type"] == "cash_sales"
    assert _stored_sales_type(db, res.json()["id"]) == "cash_sales"


def test_staff_update_changes_the_sf_sales_type(db, monkeypatch):
    row = _request(db, sales_type="cash_sales")
    db.commit()

    res = _client(db, monkeypatch).put(f"{BASE}/{row.id}", json={"sales_type": "project"})

    assert res.status_code == 200, res.text
    assert _stored_sales_type(db, row.id) == "project"


def test_an_sf_saves_with_no_sales_type(db, monkeypatch):
    """ST4: optional on an SF (a free-text customer has nothing to map from)."""
    body = {"request_type": "sponsorship_form", "customer_name": "FREE TEXT CO", "products": []}

    res = _client(db, monkeypatch).post(f"{BASE}/", json=body)

    assert res.status_code == 201, res.text
    assert _stored_sales_type(db, res.json()["id"]) is None


def test_the_portal_sf_allowlist_carries_sales_type():
    from app.services.portal_service import PortalService

    assert "sales_type" in PortalService._editable_fields(PortalService.__new__(PortalService), "sponsorship_form")


# --- ST5: revision viewer + PDF ---------------------------------------------- #

def test_the_sf_revision_viewer_shows_sales_type_after_sponsor_subject():
    from app.services.portal_revision_service import ADAPTERS

    sf = ADAPTERS["sponsorship_form"]
    fields = list(sf.snapshot_form_fields)
    assert "sales_type" in fields
    assert fields.index("sales_type") == fields.index("sponsor_subject_other") + 1
    assert sf.lookup_fields.get("sales_type") == "procurement_sales_type"
    # The SF's own lookup keeps resolving too.
    assert sf.lookup_fields.get("sponsor_subject") == "procurement_sponsor_subject"


def _bind_sales_type_label(db):
    from app.models.lookup import LookupBinding, LookupOption, LookupSet

    lset = LookupSet(id=str(uuid.uuid4()), set_key=f"zzt_sales_{uuid.uuid4().hex[:6]}",
                     name="ZZT Sales Type", is_active=True)
    db.add(lset)
    db.flush()
    db.add(LookupOption(id=str(uuid.uuid4()), set_id=lset.id,
                        value="cash_sales", label="Cash Sales", is_active=True))
    db.add(LookupBinding(id=str(uuid.uuid4()), set_id=lset.id,
                         table_name="purchase_requests", column_name="sales_type"))
    db.flush()


def test_the_printed_sf_shows_sales_type_after_sponsor_subject(db):
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    _bind_sales_type_label(db)
    row = _request(db, sponsor_subject="showroom", sales_type="cash_sales")
    db.commit()

    html = PurchaseRequestPDFService(db)._html(row)

    assert "Sales Type:" in html
    assert "Cash Sales" in html
    assert "cash_sales" not in html, "the raw code reached the printed page"
    assert html.index("Sponsor Subject:") < html.index("Sales Type:") < html.index("Date of Delivery:")


# --- ST7: backfill script ----------------------------------------------------- #

@pytest.fixture
def scenario(db):
    set_company_scope(db, None)  # the script runs with no principal: all companies
    project = _customer(db, f"300-ZZT{_sfx()}", "ZZT PROJECT CO", debtor_type="project")
    pcash = _customer(db, f"302-ZZT{_sfx()}", "ZZT PROJECT CASH CO", debtor_type="project_cash")
    cash = _customer(db, f"301-ZZT{_sfx()}", "ZZT CASH CO", debtor_type="cash_sales")
    untyped = _customer(db, f"300-ZZT{_sfx()}", "ZZT UNTYPED CO")
    # The Customer insert listener fills a missing debtor type, so only a hand-cleared row is
    # blank (app/models/order.py:184): clear it the same way.
    db.execute(text("UPDATE customers SET debtor_type = NULL WHERE id = :i"), {"i": str(untyped.id)})
    db.expire(untyped)
    ids = {
        "sf_project": _request(db, customer_id=project.id).id,
        "sf_pcash": _request(db, customer_id=pcash.id).id,
        "sf_cash": _request(db, customer_id=cash.id).id,
        "sf_untyped": _request(db, customer_id=untyped.id).id,
        "sf_unlinked": _request(db).id,
        "sf_already": _request(db, customer_id=pcash.id, sales_type="project").id,
        "pr_blank": _request(db, request_type="purchase_request", customer_id=project.id).id,
    }
    db.commit()
    return ids


def _values(db, ids) -> dict:
    return {k: _stored_sales_type(db, v) for k, v in ids.items()}


def test_backfill_dry_run_reports_counts_and_writes_nothing(db, scenario):
    from scripts.backfill_sf_sales_type import backfill

    before = _values(db, scenario)
    result = backfill(db, apply=False)

    assert result == {
        "candidates": 4,
        "fill": {"project": 1, "cash_sales": 2},
        "skipped_no_debtor_type": 1,
        "updated": 0,
    }
    assert _values(db, scenario) == before


def test_backfill_apply_fills_only_linked_sfs_with_no_sales_type(db, scenario):
    from scripts.backfill_sf_sales_type import backfill

    result = backfill(db, apply=True)

    assert result["updated"] == 3
    assert _values(db, scenario) == {
        "sf_project": "project",
        "sf_pcash": "cash_sales",
        "sf_cash": "cash_sales",
        "sf_untyped": None,
        "sf_unlinked": None,
        "sf_already": "project",
        "pr_blank": None,
    }


def test_backfill_is_idempotent(db, scenario):
    from scripts.backfill_sf_sales_type import backfill

    backfill(db, apply=True)
    again = backfill(db, apply=True)

    assert again["updated"] == 0
    assert again["fill"] == {}
    assert again["skipped_no_debtor_type"] == 1
