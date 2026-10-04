"""Phase 2 RED tests - the customer's Account level setting (ACCOUNT-LEDGER, AC-1, AC-2).

`customers.account_level` (SMALLINT, nullable, CHECK >= 1) says which numbered account a ledger
is. Contract (`documentation/plans/chatbot/PLAN-account-ledger-2oct.md`, Design 1 and 2):

* `CustomerResponse` carries `account_level`; `CustomerCreate` and `CustomerUpdate` accept it.
* PUT stores it, `null` clears it, omitting it leaves it alone, 0 is a 422.
* The PUT route itself checks sign-in only today; it must refuse a CHANGE to `account_level`
  without `order_management.customers.edit` (403, value unchanged). The SAME value or an omitted
  field is allowed as before. The rest of the route is left as it is.
* The change is in the customer's audit history (`account_level` is in `__audit_columns__`).

Postgres only, every row seeded here. Levels are set with raw SQL so a missing column reads as a
database error rather than a silently set Python attribute.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.audit_context import AuditActor, clear_actor, stamp_actor
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.audit import AuditLog
from app.models.base import set_company_scope
from app.models.order import Customer
from app.schemas.order import CustomerCreate, CustomerUpdate
from app.services.audit_service import register_audit_listeners
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.company_scope_resolver import apply_company_scope
from app.services.order_service import CustomerService
from app.services.user_service import UserPermissionService

from ._pg_fixture import blank_session, unique_code

EDIT = "order_management.customers.edit"
BASE = "/api/v1/order-management/customers"


@pytest.fixture(autouse=True)
def _listeners():
    register_company_scope_listeners()
    register_audit_listeners()


@pytest.fixture(autouse=True)
def _clean():
    yield
    app.dependency_overrides.clear()
    clear_actor()


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def _client(db, monkeypatch, *, granted: frozenset[str] = frozenset({EDIT})) -> TestClient:
    principal = {"id": str(uuid.uuid4()), "email": "zzt-account-level@test.com"}

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in granted,
    )
    return TestClient(app)


def _customer(db, level: int | None = None) -> str:
    row = CustomerService(db).create_customer(
        CustomerCreate(customer_code=unique_code("C")[:50], customer_name="ZZT Ledger Trading")
    )
    cid = str(row.id)
    if level is not None:
        db.execute(text("UPDATE customers SET account_level = :n WHERE id = :i"), {"n": level, "i": cid})
        db.expire_all()
    return cid


def _stored(db, cid: str):
    return db.execute(text("SELECT account_level FROM customers WHERE id = :i"), {"i": cid}).scalar_one()


# ------------------------------------------------------------------ shape


def test_get_returns_account_level(db, monkeypatch):
    cid = _customer(db, 3)
    resp = _client(db, monkeypatch).get(f"{BASE}/{cid}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["account_level"] == 3


def test_get_returns_null_when_unset(db, monkeypatch):
    cid = _customer(db)
    resp = _client(db, monkeypatch).get(f"{BASE}/{cid}")
    assert resp.status_code == 200, resp.text
    assert "account_level" in resp.json()
    assert resp.json()["account_level"] is None


def test_list_row_carries_account_level(db, monkeypatch):
    cid = _customer(db, 2)
    code = db.execute(text("SELECT customer_code FROM customers WHERE id = :i"), {"i": cid}).scalar_one()
    resp = _client(db, monkeypatch).get(BASE, params={"query": code})
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert [r["account_level"] for r in rows] == [2]


# ------------------------------------------------------------------ PUT: set, clear, omit, invalid


def test_put_sets_the_level(db, monkeypatch):
    cid = _customer(db)
    resp = _client(db, monkeypatch).put(f"{BASE}/{cid}", json={"account_level": 2})
    assert resp.status_code == 200, resp.text
    assert resp.json()["account_level"] == 2
    db.expire_all()
    assert _stored(db, cid) == 2


def test_put_null_clears_the_level(db, monkeypatch):
    cid = _customer(db, 2)
    resp = _client(db, monkeypatch).put(f"{BASE}/{cid}", json={"account_level": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["account_level"] is None
    db.expire_all()
    assert _stored(db, cid) is None


def test_put_omitting_the_field_leaves_it_unchanged(db, monkeypatch):
    cid = _customer(db, 2)
    resp = _client(db, monkeypatch).put(f"{BASE}/{cid}", json={"customer_name": "ZZT Renamed Trading"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["account_level"] == 2
    db.expire_all()
    assert _stored(db, cid) == 2


@pytest.mark.parametrize("bad", [0, -1])
def test_put_below_one_is_422(db, monkeypatch, bad):
    cid = _customer(db, 2)
    resp = _client(db, monkeypatch).put(f"{BASE}/{cid}", json={"account_level": bad})
    assert resp.status_code == 422, resp.text
    db.expire_all()
    assert _stored(db, cid) == 2


# ------------------------------------------------------------------ PUT: who may change it (AC-2)


def test_without_the_permission_a_change_is_403_and_the_value_stays(db, monkeypatch):
    cid = _customer(db, 1)
    resp = _client(db, monkeypatch, granted=frozenset()).put(f"{BASE}/{cid}", json={"account_level": 2})
    assert resp.status_code == 403, resp.text
    assert EDIT in resp.text
    db.expire_all()
    assert _stored(db, cid) == 1


def test_without_the_permission_clearing_is_a_change_and_403(db, monkeypatch):
    cid = _customer(db, 1)
    resp = _client(db, monkeypatch, granted=frozenset()).put(f"{BASE}/{cid}", json={"account_level": None})
    assert resp.status_code == 403, resp.text
    db.expire_all()
    assert _stored(db, cid) == 1


def test_without_the_permission_setting_one_on_an_unset_ledger_is_403(db, monkeypatch):
    cid = _customer(db)
    resp = _client(db, monkeypatch, granted=frozenset()).put(f"{BASE}/{cid}", json={"account_level": 1})
    assert resp.status_code == 403, resp.text
    db.expire_all()
    assert _stored(db, cid) is None


def test_without_the_permission_the_same_value_is_allowed_as_today(db, monkeypatch):
    cid = _customer(db, 2)
    resp = _client(db, monkeypatch, granted=frozenset()).put(
        f"{BASE}/{cid}", json={"account_level": 2, "customer_name": "ZZT Same Level Trading"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["account_level"] == 2
    assert resp.json()["customer_name"] == "ZZT Same Level Trading"


def test_without_the_permission_null_on_an_unset_ledger_is_not_a_change(db, monkeypatch):
    cid = _customer(db)
    resp = _client(db, monkeypatch, granted=frozenset()).put(f"{BASE}/{cid}", json={"account_level": None})
    assert resp.status_code == 200, resp.text


def test_without_the_permission_omitting_the_field_is_allowed_as_today(db, monkeypatch):
    cid = _customer(db, 2)
    resp = _client(db, monkeypatch, granted=frozenset()).put(
        f"{BASE}/{cid}", json={"customer_name": "ZZT Rename Only Trading"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["account_level"] == 2


def test_with_the_permission_a_change_is_200(db, monkeypatch):
    cid = _customer(db, 1)
    resp = _client(db, monkeypatch, granted=frozenset({EDIT})).put(f"{BASE}/{cid}", json={"account_level": 4})
    assert resp.status_code == 200, resp.text
    assert resp.json()["account_level"] == 4


# ------------------------------------------------------------------ POST


def test_post_accepts_account_level(db, monkeypatch):
    resp = _client(db, monkeypatch).post(
        f"{BASE}/",
        json={"customer_code": unique_code("C")[:50], "customer_name": "ZZT Created Trading", "account_level": 3},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["account_level"] == 3
    db.expire_all()
    assert _stored(db, resp.json()["id"]) == 3


def test_post_below_one_is_422(db, monkeypatch):
    resp = _client(db, monkeypatch).post(
        f"{BASE}/",
        json={"customer_code": unique_code("C")[:50], "customer_name": "ZZT Bad Level Trading", "account_level": 0},
    )
    assert resp.status_code == 422, resp.text


def test_service_create_and_update_carry_the_level(db):
    service = CustomerService(db)
    created = service.create_customer(
        CustomerCreate(customer_code=unique_code("C")[:50], customer_name="ZZT Service Trading", account_level=5)
    )
    assert created.account_level == 5
    updated = service.update_customer(str(created.id), CustomerUpdate(account_level=None))
    assert updated.account_level is None


# ------------------------------------------------------------------ audit


def test_a_level_change_is_in_the_customers_history(db):
    editor = str(uuid.uuid4())
    service = CustomerService(db)
    cid = str(
        service.create_customer(
            CustomerCreate(customer_code=unique_code("C")[:50], customer_name="ZZT Audited Trading")
        ).id
    )
    stamp_actor(AuditActor(actor_type="user", user_id=editor, real_user_id=editor, ip_address="10.0.0.9"))
    service.update_customer(cid, CustomerUpdate(account_level=2))

    updates = [
        r for r in db.query(AuditLog).filter(AuditLog.entity_id == cid).order_by(AuditLog.changed_at) if r.action == "UPDATE"
    ]
    assert len(updates) == 1, [r.action for r in db.query(AuditLog).filter(AuditLog.entity_id == cid)]
    assert updates[0].old_values["account_level"] is None
    assert updates[0].new_values["account_level"] == 2
    assert updates[0].user_id == editor


def test_account_level_is_an_audited_column():
    assert "account_level" in Customer.__audit_columns__


# ------------------------------------------------------------------ fix round 1: S1 (create) and N1 (upper bound)


def _post(db, monkeypatch, body: dict, *, granted: frozenset[str] = frozenset({EDIT})):
    return _client(db, monkeypatch, granted=granted).post(f"{BASE}/", json=body)


def _body(**extra) -> dict:
    return {"customer_code": unique_code("C")[:50], "customer_name": "ZZT Create Gate Trading", **extra}


def _count(db, code: str) -> int:
    return db.execute(text("SELECT count(*) FROM customers WHERE customer_code = :c"), {"c": code}).scalar_one()


def test_s1_post_with_a_level_without_the_permission_is_403_and_creates_nothing(db, monkeypatch):
    body = _body(account_level=2)
    resp = _post(db, monkeypatch, body, granted=frozenset())
    assert resp.status_code == 403, resp.text
    assert EDIT in resp.text
    assert _count(db, body["customer_code"]) == 0


def test_s1_post_without_a_level_or_with_null_is_allowed_without_the_permission(db, monkeypatch):
    assert _post(db, monkeypatch, _body(), granted=frozenset()).status_code == 201
    assert _post(db, monkeypatch, _body(account_level=None), granted=frozenset()).status_code == 201


def test_s1_post_with_a_level_and_the_permission_is_201_and_stored(db, monkeypatch):
    resp = _post(db, monkeypatch, _body(account_level=4), granted=frozenset({EDIT}))
    assert resp.status_code == 201, resp.text
    assert resp.json()["account_level"] == 4
    db.expire_all()
    assert _stored(db, resp.json()["id"]) == 4


def test_n1_put_above_nine_is_422(db, monkeypatch):
    cid = _customer(db, 2)
    resp = _client(db, monkeypatch).put(f"{BASE}/{cid}", json={"account_level": 10})
    assert resp.status_code == 422, resp.text
    db.expire_all()
    assert _stored(db, cid) == 2


def test_n1_post_above_nine_is_422(db, monkeypatch):
    resp = _post(db, monkeypatch, _body(account_level=10))
    assert resp.status_code == 422, resp.text


def test_n1_the_cap_is_nine_to_match_the_form(db, monkeypatch):
    cid = _customer(db)
    resp = _client(db, monkeypatch).put(f"{BASE}/{cid}", json={"account_level": 9})
    assert resp.status_code == 200, resp.text
    assert resp.json()["account_level"] == 9
