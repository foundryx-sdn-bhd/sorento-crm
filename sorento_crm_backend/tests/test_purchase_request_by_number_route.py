"""REF-PROBES AC-19: a read-only purchase request lookup by number, plus its MCP read tool.

* `GET /api/v1/procurement/purchase-requests/by-number/{number}` on the existing procurement
  router and auth (`get_current_user_or_api_key`); returns the request with its lines; 404 for
  an unknown number; no write method on the path.
* MCP tool `crm_purchase_request_by_number`: a catalogue read (`read_only` True or GET) that is
  also in the chatbot's frozen read-only allow-list.

Postgres only; rows seeded on a blank schema.
"""
from __future__ import annotations

import json
import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import dependencies as deps
from app.main import app
from app.models.procurement import PurchaseRequestHeader, PurchaseRequestLine
from tests._pg_fixture import blank_session, unique_code

PATH = "/api/v1/procurement/purchase-requests/by-number/{number}"
TOOL = "crm_purchase_request_by_number"


@pytest.fixture
def client_and_db() -> Iterator[tuple[TestClient, object]]:
    with blank_session() as sess:
        def _override_db():
            yield sess

        app.dependency_overrides[deps.get_db] = _override_db
        app.dependency_overrides[deps.get_current_user_or_api_key] = lambda: {"id": "u-admin"}
        app.dependency_overrides[deps.get_current_user] = lambda: {"id": "u-admin"}
        try:
            yield TestClient(app), sess
        finally:
            for dep in (deps.get_db, deps.get_current_user_or_api_key, deps.get_current_user):
                app.dependency_overrides.pop(dep, None)


def _seed(db, number: str) -> None:
    pr = PurchaseRequestHeader(
        id=str(uuid.uuid4()), request_type="purchase_request", request_number=number,
        customer_name="ZZT Project Sdn Bhd", status="processed_by_cs", approval_status="approved",
    )
    db.add(pr)
    db.flush()
    db.add(PurchaseRequestLine(id=str(uuid.uuid4()), purchase_request_id=pr.id, item_code="ZZT BASIN", quantity=10, sort_order=0))
    db.commit()


def test_by_number_returns_the_request_and_lines(client_and_db):
    client, db = client_and_db
    number = f"ZZTPR{unique_code()[:5].upper()}"
    _seed(db, number)
    resp = client.get(PATH.format(number=number))
    assert resp.status_code == 200, resp.text
    blob = json.dumps(resp.json())
    assert number in blob and "ZZT BASIN" in blob and "ZZT Project Sdn Bhd" in blob, blob


def test_by_number_unknown_is_404(client_and_db):
    client, _ = client_and_db
    assert client.get(PATH.format(number="ZZT-NO-SUCH-PR")).status_code == 404


def test_by_number_has_no_write_method(client_and_db):
    client, _ = client_and_db
    routes = [r for r in app.routes if getattr(r, "path", None) == "/api/v1/procurement/purchase-requests/by-number/{number}"]
    assert routes, "route not registered"
    assert set().union(*(r.methods for r in routes)) <= {"GET", "HEAD"}
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(PATH.format(number="ZZT-X")).status_code in (404, 405, 422)


def test_by_number_requires_auth():
    with blank_session() as sess:
        def _override_db():
            yield sess

        app.dependency_overrides[deps.get_db] = _override_db
        try:
            resp = TestClient(app).get(PATH.format(number="ZZT-X"))
        finally:
            app.dependency_overrides.pop(deps.get_db, None)
    assert resp.status_code in (401, 403), resp.status_code


def test_mcp_tool_is_a_catalogue_read_and_in_the_chatbot_allow_list():
    from app.services.chatbot.lanes.business.fetch import CHATBOT_READ_ONLY_TOOLS
    from app.services.mcp_tool_capability_service import _load_catalog_specs

    specs = {s.name: s for s in _load_catalog_specs()}
    assert TOOL in specs, f"{TOOL} missing from the MCP catalogue"
    spec = specs[TOOL]
    assert str(spec.method).upper() == "GET" or spec.read_only is True
    assert "purchase-requests/by-number" in str(spec.path)
    assert TOOL in CHATBOT_READ_ONLY_TOOLS
