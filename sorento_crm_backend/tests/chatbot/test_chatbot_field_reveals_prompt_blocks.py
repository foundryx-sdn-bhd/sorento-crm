"""PARSER-PER-AUDIENCE, AC-PA-2 and AC-PA-4: `GET /system/chatbot/field-reveal-keys` tells
the Field reveals card which chatbot prompt blocks a key also removes, and the
`purchase_orders.placed` switch carries its new label.

Phase 2 red: the endpoint has no `prompt_blocks` yet. Same client/auth fixtures as
`tests/chatbot/test_chatbot_field_reveals_api.py`. No em or en dashes.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.services.contact_field_reveal_service import FIELD_REVEAL_KEYS
from app.services.user_service import UserPermissionService

from tests.chatbot.test_turns_admin_api import db  # noqa: F401 - reuses the blank-schema fixture

URL = "/api/v1/system/chatbot/field-reveal-keys"
_ACTOR: dict = {"id": None, "name": "ZZT Prompt Blocks Tester"}


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug == "user_management.contacts.view",
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())


@pytest.fixture()
def client(db):  # noqa: F811 - fixture shadow is the point
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


def _items(client) -> dict[str, dict]:
    resp = client.get(URL)
    assert resp.status_code == 200, resp.text
    return {item["key"]: item for item in resp.json()["items"]}


def test_every_key_carries_a_prompt_blocks_list(client) -> None:
    items = _items(client)
    assert set(items) == {k for k, _ in FIELD_REVEAL_KEYS}
    for key, item in items.items():
        assert isinstance(item.get("prompt_blocks"), list), (key, item)


@pytest.mark.parametrize(
    "key,blocks",
    [
        ("sales_orders.sales_report", ["SALES REPORT", "SALES ANALYSIS", "TOP SELLING"]),
        ("purchase_orders.cost", ["LAST PURCHASE COST"]),
        ("scm.low_stock_report", ["LOW STOCK REPORT"]),
    ],
)
def test_a_gated_key_lists_its_blocks_in_order(client, key, blocks) -> None:
    assert _items(client)[key]["prompt_blocks"] == blocks


def test_placed_lists_the_blocks_of_both_its_rows(client) -> None:
    # purchase_order and spo_allocation both ride this grant, in table order
    assert _items(client)["purchase_orders.placed"]["prompt_blocks"] == [
        "PURCHASE ORDERS", "SPO LAST RECEIPT",
    ]


@pytest.mark.parametrize("key", ["inventory.sellable", "purchase_orders.supplier"])
def test_a_key_with_no_gate_row_has_no_blocks(client, key) -> None:
    assert _items(client)[key]["prompt_blocks"] == []


def test_placed_is_relabelled(client) -> None:
    items = _items(client)
    assert items["purchase_orders.placed"]["label"] == (
        "Purchase orders (PO asks, and PO on stock answers)"
    )
    assert dict(FIELD_REVEAL_KEYS)["purchase_orders.placed"] == (
        "Purchase orders (PO asks, and PO on stock answers)"
    )


def test_the_endpoint_still_requires_the_permission(client, monkeypatch) -> None:
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: False)
    assert client.get(URL).status_code == 403
