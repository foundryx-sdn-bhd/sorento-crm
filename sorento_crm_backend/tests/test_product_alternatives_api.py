"""PRODUCT-ALTERNATIVES: the product page's Alternatives section API (card Q1 (a), Q5 (a)).

    GET    /api/v1/master-data/products/{id}/alternatives
           -> {"data": {"alternatives_manual": bool, "alternatives": [row]}}
    POST   /api/v1/master-data/products/{id}/alternatives  {"alternative_product_id"} -> 201 row
    POST   /api/v1/master-data/products/{id}/alternatives/reset-auto -> 200 (same shape as GET)
    Unlink = server-deferred pending action `product_alternative.unlink` on the link row
    (`/api/v1/pending-actions`), reversible window.

row = {id, alternative_product_id, product_code, product_name, source, is_discontinued}.
Read gated on `master_data.products.view`, writes on `master_data.products.edit` (the
companions precedent). Any hand edit (add, unlink) sets `products.alternatives_manual`, so the
-NEW rule never touches that product again; Reset to auto clears it and re-runs the rule.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.main import app  # noqa: E402

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key
from app.models.base import set_company_scope
from app.models.product import Product, ProductAlternative
from app.models.user import User, UserStatus
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.product_alternative_service import (
    SOURCE_AUTO_NEW,
    SOURCE_MANUAL,
    reconcile_new_alternatives,
)
from app.services.record_actions import _unlink_product_alternative
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha
from tests._pg_fixture import blank_session, unique_code

VIEW = "master_data.products.view"
EDIT = "master_data.products.edit"


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


def _client(db, allow: set[str], monkeypatch, company_id=DEFAULT_COMPANY_ID) -> TestClient:
    user = User(
        id=str(uuid.uuid4()),
        email=f"{unique_code('alt-caller')}@zzt.test",
        name="ZZT Alt Caller",
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    caller = {"id": str(user.id), "email": user.email}

    def _override_db():
        yield db

    def _override_scope(_db=Depends(get_db)):
        scope = frozenset({company_id})
        set_company_scope(_db, scope)
        return scope

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: caller
    app.dependency_overrides[get_current_user_or_api_key] = lambda: caller
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in allow
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear():
    yield
    for dep in (get_db, get_current_user, get_current_user_or_api_key, apply_company_scope):
        app.dependency_overrides.pop(dep, None)


def _base(p) -> str:
    return f"/api/v1/master-data/products/{p.id}/alternatives"


@pytest.fixture()
def pair(db):
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    stem = unique_code("ZZALTAPI")[:30].upper()
    old = product(db, company_id=DEFAULT_COMPANY_ID, code=stem)
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-NEW")
    reconcile_new_alternatives(db, new)
    db.flush()
    return old, new


def test_get_lists_the_auto_link(db, pair, monkeypatch):
    old, new = pair
    response = _client(db, {VIEW}, monkeypatch).get(_base(old))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["alternatives_manual"] is False
    [row] = data["alternatives"]
    assert row["alternative_product_id"] == new.id
    assert row["product_code"] == new.product_code
    assert row["source"] == SOURCE_AUTO_NEW
    assert row["is_discontinued"] is False
    assert row["id"]


def test_get_requires_view(db, pair, monkeypatch):
    old, _ = pair
    assert _client(db, set(), monkeypatch).get(_base(old)).status_code == 403


def test_add_requires_edit(db, pair, monkeypatch):
    old, _ = pair
    other = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZALTX"))
    response = _client(db, {VIEW}, monkeypatch).post(
        _base(old), json={"alternative_product_id": other.id}
    )
    assert response.status_code == 403


def test_add_manual_link_marks_product_manual(db, pair, monkeypatch):
    old, new = pair
    other = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZALTX"))
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _base(old), json={"alternative_product_id": other.id}
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["source"] == SOURCE_MANUAL
    db.refresh(old)
    assert old.alternatives_manual is True
    codes = {
        r.alternative_product_id
        for r in db.query(ProductAlternative).filter(ProductAlternative.product_id == old.id)
    }
    assert codes == {new.id, other.id}


def test_add_refuses_self_duplicate_and_missing(db, pair, monkeypatch):
    old, new = pair
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    assert client.post(_base(old), json={"alternative_product_id": old.id}).status_code == 400
    assert client.post(_base(old), json={"alternative_product_id": new.id}).status_code == 409
    missing = str(uuid.uuid4())
    assert client.post(_base(old), json={"alternative_product_id": missing}).status_code == 404


def test_add_refuses_another_companys_product(db, pair, monkeypatch):
    old, _ = pair
    seed_mocha(db)
    foreign = product(db, company_id=MOCHA_ID, code=unique_code("ZZALTM"))
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _base(old), json={"alternative_product_id": foreign.id}
    )
    assert response.status_code == 404


def test_unlink_handler_removes_the_link_and_marks_manual(db, pair):
    old, new = pair
    link = db.query(ProductAlternative).filter(ProductAlternative.product_id == old.id).one()
    _unlink_product_alternative(db, {"entity_id": link.id})
    db.flush()
    assert db.query(ProductAlternative).filter(ProductAlternative.product_id == old.id).count() == 0
    db.refresh(old)
    assert old.alternatives_manual is True
    # The rule never re-adds what a person removed.
    reconcile_new_alternatives(db, new)
    assert db.query(ProductAlternative).filter(ProductAlternative.product_id == old.id).count() == 0


def test_unlink_is_a_registered_reversible_record_action(db, pair, monkeypatch):
    from app.services.form_action_grace import WINDOW_REVERSIBLE
    from app.services.form_action_registry import get_action

    action = get_action("product_alternative.unlink")
    assert action.permission == EDIT
    assert action.window == WINDOW_REVERSIBLE
    assert "product_alternative" in action.entity_types


def test_reset_auto_clears_manual_and_rederives(db, pair, monkeypatch):
    old, new = pair
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    other = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZALTX"))
    client.post(_base(old), json={"alternative_product_id": other.id})
    link = (
        db.query(ProductAlternative)
        .filter(ProductAlternative.product_id == old.id, ProductAlternative.alternative_product_id == new.id)
        .one()
    )
    _unlink_product_alternative(db, {"entity_id": link.id})
    db.flush()

    response = client.post(f"{_base(old)}/reset-auto")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["alternatives_manual"] is False
    assert [(r["alternative_product_id"], r["source"]) for r in data["alternatives"]] == [
        (new.id, SOURCE_AUTO_NEW)
    ]


def test_unlink_parks_through_pending_actions_and_refuses_unknown_link(db, pair, monkeypatch):
    old, _ = pair
    link = db.query(ProductAlternative).filter(ProductAlternative.product_id == old.id).one()
    client = _client(db, {VIEW, EDIT}, monkeypatch)

    def park(entity_id):
        return client.post(
            "/api/v1/pending-actions",
            json={
                "action_key": "product_alternative.unlink",
                "entity_type": "product_alternative",
                "entity_id": entity_id,
                "payload": {},
            },
        )

    parked = park(link.id)
    assert parked.status_code == 202, parked.text
    # Nothing goes until the window lapses.
    assert db.query(ProductAlternative).filter(ProductAlternative.id == link.id).count() == 1
    assert park(str(uuid.uuid4())).status_code == 404
    assert park("not-a-uuid").status_code in (400, 404, 422)
    assert _client(db, {VIEW}, monkeypatch).post(
        "/api/v1/pending-actions",
        json={
            "action_key": "product_alternative.unlink",
            "entity_type": "product_alternative",
            "entity_id": link.id,
            "payload": {},
        },
    ).status_code == 403
