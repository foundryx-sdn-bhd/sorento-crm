"""RED tests - FORM-ROUTING-CONDITIONS review round (UAC at cf68c605a): NC-8, DT-4 gate,
PC-2/PC-3/PC-4 hardening, RC-2 failure, DT-2 parity.

Postgres only, every row seeded here, fake names. Helpers are borrowed from the round-1 files.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app  # noqa: E402

from app.models.base import set_company_scope
from app.models.company import Company
from app.models.order import Customer
from app.models.procurement import PurchaseRequestHeader
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.error_handler import AppException
from tests._pg_fixture import blank_session

from tests.test_customer_account_level import _client as _cust_client  # noqa: F401
from tests.test_portal_form_customer_link import _contact, _draft, _portal_client
from tests.test_pr_customer_lookup_internal import _client as _staff_client
from tests.test_purchase_request_status_not_editable import _FakeSend, _reply, _request

PORTAL = "/api/v1/public/portal"
PR = "/api/v1/procurement/purchase-requests"
CUSTOMERS = "/api/v1/order-management/customers"
EDIT = "order_management.customers.edit"


def _sfx() -> str:
    return uuid.uuid4().hex[:6].upper()


@pytest.fixture(autouse=True)
def _clean():
    register_company_scope_listeners()
    yield
    app.dependency_overrides.clear()


def _company(db) -> str:
    c = Company(id=str(uuid.uuid4()), name="ZZT Second Co", code=f"ZZTC{_sfx()}")
    db.add(c)
    db.flush()
    return c.id


def _cust(db, code, name, *, company_id=DEFAULT_COMPANY_ID, **kw) -> Customer:
    set_company_scope(db, None)
    row = Customer(customer_code=code, customer_name=name, company_id=company_id, **kw)
    db.add(row)
    db.flush()
    return row


def _stored(db, table, col, rid):
    v = db.execute(text(f"SELECT {col} FROM {table} WHERE id = :i"), {"i": str(rid)}).scalar_one()
    return None if v is None else str(v)


# ------------------------------------------------------------------ 1. NC-8 report


def test_nc8_report_counts_completed_by_default_and_offers_it():
    from app.services.reports.datasets import sponsorship_forms as ds

    assert "completed" in ds.DEFAULT_STATUSES
    assert ("completed", "Completed") in list(ds.STATUS_LABELS)
    with blank_session() as db:
        assert ("completed", "Completed") in list(ds.status_options(db))


def test_nc8_a_completed_sponsorship_form_matches_the_default_status_filter():
    from app.services.reports.datasets import sponsorship_forms as ds

    with blank_session() as db:
        ids = {}
        for status in ("completed", "draft"):
            pid = str(uuid.uuid4())
            db.add(PurchaseRequestHeader(id=pid, request_type="sponsorship_form", status=status, source="manual"))
            ids[status] = pid
        db.flush()
        cond = ds.status_condition(None, list(ds.DEFAULT_STATUSES))
        hit = {
            str(r[0])
            for r in db.execute(
                text("SELECT id FROM purchase_requests WHERE id IN (:a, :b) AND " + str(
                    cond.compile(compile_kwargs={"literal_binds": True})
                ).replace("purchase_requests.", "purchase_requests."))
                .bindparams(a=ids["completed"], b=ids["draft"])
            )
        }
        assert hit == {ids["completed"]}


# ------------------------------------------------------------------ 2. DT-4 gate


@pytest.fixture
def cdb():
    register_company_scope_listeners()
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def _gate_client(db, monkeypatch, granted):
    return _cust_client(db, monkeypatch, granted=frozenset(granted))


def _mk(db, debtor_type):
    row = _cust(db, f"300-ZZT{_sfx()}", "ZZT Gate Trading", debtor_type=debtor_type)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    return str(row.id)


def test_dt4_change_without_permission_is_403_and_value_stays(cdb, monkeypatch):
    cid = _mk(cdb, "project")
    r = _gate_client(cdb, monkeypatch, ()).put(f"{CUSTOMERS}/{cid}", json={"debtor_type": "cash_sales"})
    assert r.status_code == 403, r.text
    assert EDIT in r.text
    cdb.expire_all()
    assert _stored(cdb, "customers", "debtor_type", cid) == "project"


def test_dt4_change_with_permission_is_200(cdb, monkeypatch):
    cid = _mk(cdb, "project")
    r = _gate_client(cdb, monkeypatch, (EDIT,)).put(f"{CUSTOMERS}/{cid}", json={"debtor_type": "cash_sales"})
    assert r.status_code == 200, r.text
    assert r.json()["debtor_type"] == "cash_sales"


def test_dt4_same_value_without_permission_is_allowed(cdb, monkeypatch):
    cid = _mk(cdb, "project")
    r = _gate_client(cdb, monkeypatch, ()).put(
        f"{CUSTOMERS}/{cid}", json={"debtor_type": "project", "customer_name": "ZZT Same Value Trading"}
    )
    assert r.status_code == 200, r.text


def test_dt4_clearing_without_permission_is_a_change_and_403(cdb, monkeypatch):
    cid = _mk(cdb, "project")
    r = _gate_client(cdb, monkeypatch, ()).put(f"{CUSTOMERS}/{cid}", json={"debtor_type": None})
    assert r.status_code == 403, r.text


def test_dt4_create_with_a_value_without_permission_is_403_and_creates_nothing(cdb, monkeypatch):
    code = f"300-ZZT{_sfx()}"
    r = _gate_client(cdb, monkeypatch, ()).post(
        f"{CUSTOMERS}/", json={"customer_code": code, "customer_name": "ZZT Create Gate", "debtor_type": "cash_sales"}
    )
    assert r.status_code == 403, r.text
    assert cdb.execute(text("SELECT count(*) FROM customers WHERE customer_code = :c"), {"c": code}).scalar_one() == 0


def test_dt4_create_with_null_or_omitted_without_permission_is_201(cdb, monkeypatch):
    c = _gate_client(cdb, monkeypatch, ())
    for extra in ({}, {"debtor_type": None}):
        r = c.post(f"{CUSTOMERS}/", json={"customer_code": f"300-ZZT{_sfx()}", "customer_name": "ZZT Open Create", **extra})
        assert r.status_code == 201, r.text


def test_dt4_create_with_a_value_and_permission_is_201(cdb, monkeypatch):
    r = _gate_client(cdb, monkeypatch, (EDIT,)).post(
        f"{CUSTOMERS}/",
        json={"customer_code": f"300-ZZT{_sfx()}", "customer_name": "ZZT Allowed Create", "debtor_type": "cash_sales"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["debtor_type"] == "cash_sales"


# ------------------------------------------------------------------ 3. PC-3 company scope (guard)


def test_other_company_customer_is_absent_from_both_lookups_and_unlinkable(monkeypatch):
    from tests._portal_grant import link_contact_company

    with blank_session() as db:
        contact = _contact(db)
        link_contact_company(db, contact.id)
        other = _company(db)
        tag = f"ZZQ{_sfx()}"
        mine = _cust(db, f"300-{tag}A", f"{tag} MINE SDN BHD")
        theirs = _cust(db, f"300-{tag}B", f"{tag} THEIRS SDN BHD", company_id=other)
        set_company_scope(db, None)

        with _portal_client(db, contact.id) as c:
            codes = {i["customer_code"] for i in c.get(f"{PORTAL}/lookups/debtors", params={"q": tag}).json()}
            assert codes == {mine.customer_code}
            unknown = _draft(c, "purchase_request", {"customer_id": str(uuid.uuid4())})
            linked = _draft(c, "purchase_request", {"customer_id": str(theirs.id)})
            assert unknown.status_code == 422, unknown.text
            assert linked.status_code == 422, linked.text
            assert linked.text == unknown.text

        staff = _staff_client(db, monkeypatch)
        items = staff.get(f"{PR}/lookups/customers", params={"q": tag}).json()
        assert {i["customer_code"] for i in items} == {mine.customer_code}
        created = staff.post(f"{PR}/", json={"request_type": "purchase_request", "customer_id": str(theirs.id)})
        assert created.status_code == 422, created.text
        ok = staff.post(f"{PR}/", json={"request_type": "purchase_request"})
        rid = ok.json()["id"]
        upd = staff.put(f"{PR}/{rid}", json={"customer_id": str(theirs.id)})
        assert upd.status_code == 422, upd.text


# ------------------------------------------------------------------ 4. portal item has no debtor_type


def test_portal_lookup_item_has_no_debtor_type_but_internal_still_has_it(monkeypatch):
    with blank_session() as db:
        contact = _contact(db)
        from tests._portal_grant import link_contact_company

        link_contact_company(db, contact.id)
        tag = f"ZZQ{_sfx()}"
        _cust(db, f"302-{tag}", f"{tag} PC SDN BHD")
        set_company_scope(db, None)
        with _portal_client(db, contact.id) as c:
            item = c.get(f"{PORTAL}/lookups/debtors", params={"q": tag}).json()[0]
        assert "debtor_type" not in item
        assert item["sales_type"] == "cash_sales"
        staff = _staff_client(db, monkeypatch)
        assert staff.get(f"{PR}/lookups/customers", params={"q": tag}).json()[0]["debtor_type"] == "project_cash"


# ------------------------------------------------------------------ 5. name derivation, "" unlink, inactive


def test_portal_save_stores_the_customers_own_name():
    with blank_session() as db:
        contact = _contact(db)
        cust = _cust(db, f"300-ZZT{_sfx()}", "ZZT REAL NAME SDN BHD")
        with _portal_client(db, contact.id) as c:
            res = _draft(c, "purchase_request", {"customer_name": "SOMETHING ELSE", "customer_id": str(cust.id)})
            assert res.status_code in (200, 201), res.text
            assert _stored(db, "purchase_requests", "customer_name", res.json()["id"]) == "ZZT REAL NAME SDN BHD"


def test_internal_create_and_update_store_the_customers_own_name(monkeypatch):
    with blank_session() as db:
        a = _cust(db, f"300-ZZT{_sfx()}", "ZZT NAME A SDN BHD")
        b = _cust(db, f"300-ZZT{_sfx()}", "ZZT NAME B SDN BHD")
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        staff = _staff_client(db, monkeypatch)
        res = staff.post(f"{PR}/", json={"request_type": "purchase_request", "customer_name": "WRONG", "customer_id": str(a.id)})
        assert res.status_code == 201, res.text
        assert res.json()["customer_name"] == "ZZT NAME A SDN BHD"
        upd = staff.put(f"{PR}/{res.json()['id']}", json={"customer_name": "WRONG", "customer_id": str(b.id)})
        assert upd.status_code == 200, upd.text
        assert upd.json()["customer_name"] == "ZZT NAME B SDN BHD"


def test_empty_string_customer_id_unlinks_on_portal_and_internal(monkeypatch):
    with blank_session() as db:
        contact = _contact(db)
        cust = _cust(db, f"300-ZZT{_sfx()}", "ZZT UNLINK SDN BHD")
        with _portal_client(db, contact.id) as c:
            sid = _draft(c, "purchase_request", {"customer_id": str(cust.id)}).json()["id"]
            res = c.put(f"{PORTAL}/submissions/purchase_request/{sid}", json={"fields": {"customer_name": "FREE", "customer_id": ""}})
            assert res.status_code == 200, res.text
            assert _stored(db, "purchase_requests", "customer_id", sid) is None
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        staff = _staff_client(db, monkeypatch)
        rid = staff.post(f"{PR}/", json={"request_type": "purchase_request", "customer_id": str(cust.id)}).json()["id"]
        upd = staff.put(f"{PR}/{rid}", json={"customer_id": ""})
        assert upd.status_code == 200, upd.text
        assert _stored(db, "purchase_requests", "customer_id", rid) is None


def test_inactive_customer_cannot_be_newly_linked_but_an_existing_link_still_saves(monkeypatch):
    with blank_session() as db:
        contact = _contact(db)
        dead = _cust(db, f"300-ZZT{_sfx()}", "ZZT DEAD SDN BHD", is_active=False)
        live = _cust(db, f"300-ZZT{_sfx()}", "ZZT LIVE SDN BHD")
        with _portal_client(db, contact.id) as c:
            assert _draft(c, "purchase_request", {"customer_id": str(dead.id)}).status_code == 422
            sid = _draft(c, "purchase_request", {"customer_id": str(live.id)}).json()["id"]
            db.execute(text("UPDATE customers SET is_active = false WHERE id = :i"), {"i": str(live.id)})
            again = c.put(f"{PORTAL}/submissions/purchase_request/{sid}", json={"fields": {"customer_id": str(live.id), "pic": "CS PIC X"}})
            assert again.status_code == 200, again.text
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        staff = _staff_client(db, monkeypatch)
        assert staff.post(f"{PR}/", json={"request_type": "purchase_request", "customer_id": str(dead.id)}).status_code == 422
        rid = staff.post(f"{PR}/", json={"request_type": "purchase_request"}).json()["id"]
        db.execute(text("UPDATE purchase_requests SET customer_id = :c WHERE id = :i"), {"c": str(live.id), "i": rid})
        db.expire_all()
        keep = staff.put(f"{PR}/{rid}", json={"customer_id": str(live.id), "pic": "CS PIC X"})
        assert keep.status_code == 200, keep.text


# ------------------------------------------------------------------ 6. update-and-reply


def _uar(customer_id):
    from app.schemas.procurement import PurchaseRequestUpdateAndReply

    return PurchaseRequestUpdateAndReply(customer_id=customer_id, reply_message="ok")


def test_update_and_reply_unknown_customer_is_422():
    with blank_session() as db:
        row = _request(db, status="submitted")
        with pytest.raises(AppException) as ei:
            _reply(db, str(row.id), _uar(str(uuid.uuid4())), _FakeSend())
        assert ei.value.status_code == 422


def test_update_and_reply_other_company_customer_is_422():
    with blank_session() as db:
        other = _company(db)
        theirs = _cust(db, f"300-ZZT{_sfx()}", "ZZT THEIRS SDN BHD", company_id=other)
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        row = _request(db, status="submitted")
        with pytest.raises(AppException) as ei:
            _reply(db, str(row.id), _uar(str(theirs.id)), _FakeSend())
        assert ei.value.status_code == 422


def test_update_and_reply_valid_customer_is_stored_and_name_derived():
    with blank_session() as db:
        mine = _cust(db, f"300-ZZT{_sfx()}", "ZZT MINE SDN BHD")
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        row = _request(db, status="submitted")
        _reply(db, str(row.id), _uar(str(mine.id)), _FakeSend())
        db.expire_all()
        assert _stored(db, "purchase_requests", "customer_id", row.id) == str(mine.id)
        assert _stored(db, "purchase_requests", "customer_name", row.id) == "ZZT MINE SDN BHD"


# ------------------------------------------------------------------ 7. revision diff


@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
def test_revision_diff_hides_customer_id_but_shows_the_name_change(kind):
    from app.services.portal_revision_service import PortalRevisionService, get_adapter

    with blank_session() as db:
        before = {"customer_name": "ZZT OLD SDN BHD", "customer_id": str(uuid.uuid4()), "products": []}
        after = {"customer_name": "ZZT NEW SDN BHD", "customer_id": str(uuid.uuid4()), "products": []}
        changes = PortalRevisionService(db)._diff(get_adapter(kind), before, after)
        fields = [c["field"] for c in changes]
        assert "customer_id" not in fields
        assert "customer_name" in fields


# ------------------------------------------------------------------ 8. RC-2 failure never completes a form


@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
def test_a_form_values_load_failure_never_completes_the_form(kind):
    from tests.test_no_cs_finish import _approve, _cs_trackers, _world

    with blank_session() as db:
        s, pid, _ = _world(db, kind, rule="no_cs")
        db.commit()  # the failure path rolls back, as in production where the form is committed
        real_execute = db.execute

        def failing(stmt, *a, **k):
            if "row_to_json" in str(stmt):
                raise RuntimeError("zzt: form values unavailable")
            return real_execute(stmt, *a, **k)

        db.execute = failing  # type: ignore[method-assign]
        try:
            header = _approve(db, kind, pid, s.contact_id)
        finally:
            db.execute = real_execute  # type: ignore[method-assign]

        assert header.status == "approved"
        assert len(_cs_trackers(db, pid)) == 1


# ------------------------------------------------------------------ 9. DT-2 parity with padded codes


@pytest.mark.parametrize(
    "code,name",
    [(" 302-ZZT009", "ZZT PADDED CODE"), (" 301-ZZTC09", "CASH (PERSON A)"), ("300-ZZT009 ", "ZZT TRAILING")],
)
def test_backfill_and_python_rule_agree_on_a_padded_customer_code(code, name):
    from app.services.debtor_type import default_debtor_type
    from tests.test_migration_frc_0001_debtor_type_no_cs import _dt, _migration_module, _pre_migration_customer

    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        cid = _pre_migration_customer(db, code, name)
        _migration_module().backfill_debtor_type(db.connection())
        assert _dt(db, cid) == default_debtor_type(code, name)
