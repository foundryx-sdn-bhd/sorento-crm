"""PRODUCT-ALTERNATIVES round B, the owner's three turns (9 Oct 2026) end to end.

Owner on the test copy after round A passed: "why ETA doesn't behave the same ah, and the photo
also why don't behave the same ... SRTWC286-SH-P is discontinued already, but why it don't find
alterantives SRTWC286-SH-NEW-P which i understand also no phto, but atl eat we need to show
that we have tried to search for it".

Every call here goes the way production goes: `business.run_fetch` -> the MCP package's own
tool code (`sorento_crm_mcp.server._execute_tool_request`, the catalog's path and query
params) -> the REAL FastAPI endpoint (so a `response_model` that drops a key drops it here
too, the round A bug) -> the MCP presenter -> the WhatsApp render. Only the HTTP hop between
the MCP process and the backend is replaced by an in-process client.

    1. dealer "eta" on SRTBF11821 (discontinued, no shipment), SRTBF11821-NEW incoming
       -> the dealer line plus `Alternative: SRTBF11821-NEW: ✅ ETA dd/mm/yyyy`
    2. "SRTBF11821 photo": the old code's photo is sent, the alternative has none
       -> `Alternative: SRTBF11821-NEW: no Product Photos.` (the search is shown)
    3. "srtwc286-sh-p photo": neither has a photo -> a miss, and the miss reply still carries
       `Alternative: SRTWC286-SH-NEW-P: no Product Photos.` before any escalate offer
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: E402  (first app import)

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import RespondContact, StockVisibilityPolicy
from app.models.base import set_company_scope
from app.models.product import ProductAlternative
from app.services.chatbot.lanes import business
from app.services.chatbot.lanes.business.services import FetchServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import (
    attachment,
    attachment_type,
    inbound_shipment,
    inbound_shipment_line,
    product,
    product_attachment,
    warehouse,
)
from tests._pg_fixture import blank_session, unique_code

_MCP_ROOT = Path(__file__).resolve().parents[3] / "sorento_crm_mcp"
if str(_MCP_ROOT) not in sys.path:
    sys.path.append(str(_MCP_ROOT))

SPACE = "364817"


@pytest.fixture()
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


@pytest.fixture()
def http(db, monkeypatch):
    """The backend as the MCP process sees it: an API-key principal allowed every read."""
    principal = {"id": str(uuid.uuid4()), "email": "zzt-alt-e2e@test.com"}

    def _override_db():
        yield db

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True)
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    try:
        yield TestClient(app)
    finally:
        for dep in (get_db, get_current_user, get_current_user_or_api_key, apply_company_scope):
            app.dependency_overrides.pop(dep, None)


def _mcp_call(http):
    """`mcp_call(name, args)` running the MCP package's own tool code against `http`."""
    from sorento_crm_mcp.catalog import CATALOG
    from sorento_crm_mcp.presenters import present_response
    from sorento_crm_mcp.server import _execute_tool_request

    class _InProcess:
        async def request(self, method, path, *, path_params=None, query=None, body=None, tool_name=None):
            for key, value in (path_params or {}).items():
                path = path.replace("{" + key + "}", str(value))
            params = {k: v for k, v in (query or {}).items() if v not in (None, "")}
            return http.request(method, path, params=params).text

        async def get(self, path, *, path_params=None, query=None, tool_name=None):
            return await self.request("GET", path, path_params=path_params, query=query)

    calls: list[tuple[str, dict]] = []

    def call(name, args):
        spec = next(s for s in CATALOG if s.name == name)
        query = {}
        for key in spec.query_params:
            value = args.get(key)
            if value is None:
                continue
            query[key] = ",".join(str(v) for v in value) if isinstance(value, list) else value
        calls.append((name, dict(query)))
        raw = asyncio.run(_execute_tool_request(spec, _InProcess(), {}, query))
        return present_response(name, raw)

    call.calls = calls  # type: ignore[attr-defined]
    return call


def _dealer(db, wh):
    contact = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT Mr Loo",
    )
    db.add(contact)
    db.flush()
    db.add(StockVisibilityPolicy(id=str(uuid.uuid4()), contact_id=contact.id, mode="availability", warehouse_ids=[wh.id]))
    db.flush()
    return contact


def _pair(db, old_code, new_code):
    old = product(db, company_id=DEFAULT_COMPANY_ID, code=old_code)
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=new_code)
    old.is_discontinued = True
    db.add(ProductAlternative(product_id=old.id, alternative_product_id=new.id, source="auto_new", company_id=DEFAULT_COMPANY_ID))
    db.flush()
    return old, new


def _run(db, http, *, domain, entities, contact=None):
    call = _mcp_call(http)
    payload = {
        "_exit_kind": "continue",
        "gate": {"compatible_entities": entities},
        "ctx": {
            "parse": {"output": {"message_type": "business_query", "domain_hint": domain, "requested_attributes": []}},
            "contact": {"id": contact.id} if contact is not None else {},
        },
    }
    fragment = business.run_fetch(payload, services=FetchServices(mcp_call=call), dry_run=False, space_id=SPACE, db=db)
    return fragment.get("fetch") or {}, call.calls


def _entity(p):
    return {"uuid": p.id, "entity_type": "product", "code": p.product_code}


def test_owner_turn_1_dealer_eta_names_the_alternatives_eta(db, http):
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZBRW")[:20])
    stem = unique_code("ZZBF")[:24].upper()
    old, new = _pair(db, stem, f"{stem}-NEW")
    att_type = attachment_type(db, name=unique_code("Packing")[:40])
    packing = attachment(db, type_id=att_type.id, filename="packing-list.pdf")
    shipment = inbound_shipment(db, company_id=DEFAULT_COMPANY_ID, eta=date(2026, 10, 20))
    shipment.attachment_id = packing.id
    shipment.shipment_status = "in_transit"
    db.flush()
    inbound_shipment_line(db, company_id=DEFAULT_COMPANY_ID, shipment_id=shipment.id, product_id=new.id)
    contact = _dealer(db, wh)

    fetch, calls = _run(db, http, domain="incoming", entities=[_entity(old)], contact=contact)

    said = str(fetch.get("response") or "")
    assert len(calls) == 2, calls
    assert f"{old.product_code}: No ETA" in said, said
    assert f"Alternative: {new.product_code}: ✅ ETA" in said, said


def _photos(db):
    return attachment_type(db, name="Product Photos")


def test_owner_turn_2_photo_with_none_for_the_alternative_shows_the_search(db, http):
    stem = unique_code("ZZBF")[:24].upper()
    old, new = _pair(db, stem, f"{stem}-NEW")
    photos = _photos(db)
    photo = attachment(db, type_id=photos.id, filename=f"{stem}-02.png")
    product_attachment(db, company_id=DEFAULT_COMPANY_ID, product_id=old.id, attachment_id=photo.id)

    fetch, calls = _run(
        db,
        http,
        domain="product_attachment",
        entities=[_entity(old), {"uuid": photos.id, "entity_type": "attachment_type", "code": "Product Photos"}],
    )

    said = str(fetch.get("response") or "")
    assert len(calls) == 2, calls
    assert f"{stem}-02.png" in said, said
    assert f"Alternative: {new.product_code}: no Product Photos." in said, said


def test_owner_turn_3_photo_miss_on_both_still_carries_the_alternative_line(db, http):
    stem = unique_code("ZZWC")[:24].upper()
    old, new = _pair(db, f"{stem}-SH-P", f"{stem}-SH-NEW-P")
    photos = _photos(db)

    fetch, calls = _run(
        db,
        http,
        domain="product_attachment",
        entities=[_entity(old), {"uuid": photos.id, "entity_type": "attachment_type", "code": "Product Photos"}],
    )

    assert len(calls) == 2, calls
    assert not fetch.get("has_result")
    # The miss reply (`answer_bridge.answer_for`) prints these before any escalate offer.
    assert fetch.get("alternative_lines") == [f"Alternative: {new.product_code}: no Product Photos."], fetch


def test_the_miss_reply_carries_the_alternative_lines_before_the_escalate_offer():
    from app.services.chatbot.answer_bridge import with_alternative_lines

    lines = ["Alternative: X-NEW: no Product Photos."]
    miss = "Sorry, no Product Photos matched X. Would you like me to escalate to Marketing team?"
    assert with_alternative_lines(miss, lines) == (
        "Sorry, no Product Photos matched X.\nAlternative: X-NEW: no Product Photos.\n"
        "Would you like me to escalate to Marketing team?"
    )
    joined = "No Product Photos for X. Do you want another type, or would you like me to escalate to Marketing team?"
    assert with_alternative_lines(joined, lines) == (
        "No Product Photos for X.\nAlternative: X-NEW: no Product Photos.\n"
        "Do you want another type, or would you like me to escalate to Marketing team?"
    )
    assert with_alternative_lines("No Product Photos for X.", lines) == (
        "No Product Photos for X.\nAlternative: X-NEW: no Product Photos."
    )
    assert with_alternative_lines("anything", []) == "anything"
