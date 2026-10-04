"""DO-ASK-SIMPLIFY owner rule (4 Oct 2026): every DO field reveal defaults to ON for EVERY
contact, new contacts included. The owner turns OFF what he wants per contact.

The other reveal keys (inventory.sellable, purchase_orders.*, sales_orders.*,
scm.low_stock_report) keep the old default: hidden unless granted.

Postgres only, via the blank-schema `session_factory` fixture in `tests/chatbot/conftest.py`.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import ContactFieldReveal
from app.services import contact_field_reveal_service as svc
from app.services.chatbot.head.access import _granted_field_reveal_keys, check_access
from app.services.user_service import UserPermissionService

from tests.chatbot.test_turns_admin_api import db  # noqa: F401 - reuses the blank-schema fixture

DO_KEYS = sorted(
    [
        "delivery_orders.status",
        "delivery_orders.pickup_time",
        "delivery_orders.transporter",
        "delivery_orders.driver",
        "delivery_orders.lorry_plate",
        "delivery_orders.order_number",
        "delivery_orders.customer",
        "delivery_orders.order_date",
        "delivery_orders.delivery_date",
        "delivery_orders.warehouse",
        "delivery_orders.products",
    ]
)
NON_DO_KEYS = [
    "purchase_orders.cost",
    "purchase_orders.supplier",
    "purchase_orders.placed",
    "inventory.sellable",
    "sales_orders.outstanding",
    "sales_orders.sales_report",
    "scm.low_stock_report",
]
WAREHOUSE = "delivery_orders.warehouse"
SPACE_ID = "364817"
BASE = "/api/v1/system/chatbot"


def _seed_contact(db, respond_io_id: str | None = None) -> tuple[str, str]:
    """A respond contact inside a workspace. Returns (internal id, respond_io_id)."""
    respond_io_id = respond_io_id or f"ZZT-{uuid.uuid4().hex[:8]}"
    workspace_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO respond_workspaces (id, space_id, api_key_ciphertext) "
            "VALUES (:id, :space_id, 'x')"
        ),
        {"id": workspace_id, "space_id": SPACE_ID},
    )
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, workspace_id, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, :wid, CAST(:sv AS jsonb))"
        ),
        {
            "cid": respond_io_id,
            "phone": f"+6010000{uuid.uuid4().int % 10000:04d}",
            "wid": workspace_id,
            "sv": json.dumps({}),
        },
    )
    db.commit()
    contact_id = db.execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :cid"), {"cid": respond_io_id}
    ).scalar()
    return contact_id, respond_io_id


@pytest.fixture()
def contact(session_factory):
    db = session_factory()
    contact_id, respond_io_id = _seed_contact(db)
    return db, contact_id, respond_io_id


def test_new_contact_holds_every_do_key_and_no_other(session_factory, contact):
    _db, contact_id, _rid = contact
    granted = svc.granted_keys(session_factory(), contact_id)
    assert granted == DO_KEYS
    for key in NON_DO_KEYS:
        assert key not in granted


def test_a_do_key_row_granted_false_is_hidden_the_rest_stay_on(session_factory, contact):
    db, contact_id, _rid = contact
    db.add(ContactFieldReveal(respond_contact_id=contact_id, field_key=WAREHOUSE, granted=False))
    db.commit()

    granted = svc.granted_keys(session_factory(), contact_id)
    assert WAREHOUSE not in granted
    assert granted == [k for k in DO_KEYS if k != WAREHOUSE]


def test_turning_one_do_key_off_writes_a_false_row(session_factory, contact):
    db, contact_id, _rid = contact
    keys = [k for k in DO_KEYS if k != WAREHOUSE]

    result = svc.set_granted_keys(db, contact_id, keys, actor_id="u-1")

    assert result == keys
    assert svc.granted_keys(session_factory(), contact_id) == keys
    row = (
        session_factory()
        .query(ContactFieldReveal)
        .filter(
            ContactFieldReveal.respond_contact_id == contact_id,
            ContactFieldReveal.field_key == WAREHOUSE,
        )
        .one()
    )
    assert row.granted is False


def test_turning_it_back_on_restores_it(session_factory, contact):
    db, contact_id, _rid = contact
    svc.set_granted_keys(db, contact_id, [k for k in DO_KEYS if k != WAREHOUSE], actor_id="u-1")

    result = svc.set_granted_keys(db, contact_id, DO_KEYS, actor_id="u-1")

    assert result == DO_KEYS
    assert svc.granted_keys(session_factory(), contact_id) == DO_KEYS


def test_an_empty_save_on_a_fresh_contact_hides_every_do_key(session_factory, contact):
    db, contact_id, _rid = contact

    result = svc.set_granted_keys(db, contact_id, [], actor_id="u-1")

    assert result == []
    assert svc.granted_keys(session_factory(), contact_id) == []
    rows = (
        session_factory()
        .query(ContactFieldReveal)
        .filter(ContactFieldReveal.respond_contact_id == contact_id)
        .all()
    )
    assert {r.field_key for r in rows} >= set(DO_KEYS)
    assert all(r.granted is False for r in rows)


def test_chatbot_access_check_returns_the_do_keys_for_a_contact_with_no_rows(session_factory, contact):
    _db, _contact_id, respond_io_id = contact

    access = check_access(
        session_factory(), agent_code="general_enquiries", contact_id=respond_io_id, space_id=SPACE_ID
    )

    assert access["attributes"] == DO_KEYS
    assert access["all_attributes_allowed"] is False


def test_chatbot_helper_returns_the_do_keys_for_a_contact_with_no_rows(session_factory, contact):
    _db, _contact_id, respond_io_id = contact

    keys = _granted_field_reveal_keys(session_factory(), contact_id=respond_io_id, space_id=SPACE_ID)

    assert sorted(keys) == DO_KEYS


# ---- Field reveals API ------------------------------------------------------

_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "ZZT Do Reveal Tester"}


@pytest.fixture()
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.update({"user_management.contacts.view", "user_management.contacts.edit"})
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in _GRANTS
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


@pytest.fixture()
def client(db, _permissions):  # noqa: F811
    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    _ACTOR["id"] = str(uuid.uuid4())
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def test_api_get_reports_the_do_keys_granted_for_a_fresh_contact(client, db):  # noqa: F811
    contact_id, _rid = _seed_contact(db)

    resp = client.get(f"{BASE}/contacts/{contact_id}/field-reveals")

    assert resp.status_code == 200, resp.text
    assert resp.json()["granted"] == DO_KEYS
