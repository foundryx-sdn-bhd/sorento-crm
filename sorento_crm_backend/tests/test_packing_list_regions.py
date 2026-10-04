"""Packing list regions (REGION-PACKING-LIST). AC-RPL-1, 3, 4, 5, 6.

UAC: documentation/plans/procurement/region-packing-list-acceptance-criteria.md.

Values are lowercase codes 'west' / 'east'. Postgres only, blank schema, every row seeded
here (CI's database is empty).
"""
from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import (
    get_current_user,
    get_current_user_or_api_key,
    get_db,
    get_external_api_user,
)
from app.models.base import set_company_scope
from app.models.procurement import InboundShipment
from app.models.resources import Attachment, AttachmentType
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import product
from tests._pg_fixture import blank_session, unique_code

STAFF = "/api/v1/procurement/packing-lists/"
EXTERNAL = "/api/v1/external/packing-lists/"
ATTACHMENTS = "/api/v1/resource-management/attachments/"


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


@pytest.fixture
def client(db, monkeypatch):
    def _override_db():
        yield db

    principal = {"id": str(uuid.uuid4()), "email": "zzt-rpl@test.com"}
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[get_external_api_user] = lambda: principal

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _attachment(db, *, regions=None) -> Attachment:
    aid = str(uuid.uuid4())
    kwargs = {} if regions is None else {"regions": regions}
    row = Attachment(
        id=aid,
        original_filename="packing-list.pdf",
        stored_filename=f"{aid}.pdf",
        file_path=f"/attachments/{aid}.pdf",
        mime_type="application/pdf",
        **kwargs,
    )
    db.add(row)
    db.flush()
    return row


def _staff_body(**extra) -> dict:
    return {
        "shipment_number": unique_code("RPL")[:40],
        "shipment_date": "2026-10-01",
        **extra,
    }


def _staff_create(client, **extra):
    return client.post(STAFF, json=_staff_body(**extra))


def _external_body(db, attachment_id, **header_extra) -> dict:
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    return {
        "packing_list": {
            "shipment_number": unique_code("EXT")[:40],
            "shipment_date": "2026-10-01",
            "attachment_id": attachment_id,
            **header_extra,
        },
        "packing_list_products": [{"product_code": p.product_code, "quantity": 5}],
    }


def _stored_regions(db, shipment_id) -> set[str]:
    db.expire_all()
    row = db.execute(
        text("select regions from inbound_shipments where id = :i"), {"i": shipment_id}
    ).scalar_one()
    return set(row)


# ----------------------------------------------------------------- AC-RPL-1 / 6 default


def test_ac_rpl_6_a_row_inserted_without_regions_reads_west(db):
    sid = str(uuid.uuid4())
    db.add(
        InboundShipment(
            id=sid,
            shipment_number=unique_code("DEF")[:40],
            shipment_date=date(2026, 1, 1),
        )
    )
    db.flush()
    assert _stored_regions(db, sid) == {"west"}


def test_ac_rpl_6_the_orm_attribute_defaults_to_west_after_refresh(db):
    sid = str(uuid.uuid4())
    row = InboundShipment(
        id=sid, shipment_number=unique_code("DEF2")[:40], shipment_date=date(2026, 1, 1)
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert set(row.regions) == {"west"}


def test_ac_rpl_1_staff_create_without_regions_is_west_only(client, db):
    res = _staff_create(client)
    assert res.status_code == 201, res.text
    body = res.json()
    assert set(body["regions"]) == {"west"}
    assert _stored_regions(db, body["id"]) == {"west"}


def test_ac_rpl_1_staff_create_with_both_regions_keeps_both(client, db):
    res = _staff_create(client, regions=["west", "east"])
    assert res.status_code == 201, res.text
    assert set(res.json()["regions"]) == {"west", "east"}
    assert _stored_regions(db, res.json()["id"]) == {"west", "east"}


def test_ac_rpl_1_duplicates_are_de_duplicated(client, db):
    res = _staff_create(client, regions=["east", "east"])
    assert res.status_code == 201, res.text
    assert sorted(res.json()["regions"]) == ["east"]
    assert sorted(_stored_regions(db, res.json()["id"])) == ["east"]


def test_ac_rpl_1_external_create_without_regions_is_west_only(client, db):
    att = _attachment(db)
    res = client.post(EXTERNAL, json=_external_body(db, att.id))
    assert res.status_code == 201, res.text
    shipment = res.json()["shipment"]
    assert set(shipment["regions"]) == {"west"}
    assert _stored_regions(db, shipment["id"]) == {"west"}


# ----------------------------------------------------------------- AC-RPL-3 precedence


def test_ac_rpl_3_payload_regions_win_over_the_attachment(client, db):
    att = _attachment(db, regions=["east"])
    res = client.post(EXTERNAL, json=_external_body(db, att.id, regions=["west"]))
    assert res.status_code == 201, res.text
    assert _stored_regions(db, res.json()["shipment"]["id"]) == {"west"}


def test_ac_rpl_3_payload_regions_win_when_the_attachment_has_none(client, db):
    att = _attachment(db)
    res = client.post(EXTERNAL, json=_external_body(db, att.id, regions=["east", "west"]))
    assert res.status_code == 201, res.text
    assert set(res.json()["shipment"]["regions"]) == {"east", "west"}


def test_ac_rpl_3_attachment_regions_apply_when_the_payload_has_none(client, db):
    att = _attachment(db, regions=["east"])
    res = client.post(EXTERNAL, json=_external_body(db, att.id))
    assert res.status_code == 201, res.text
    assert set(res.json()["shipment"]["regions"]) == {"east"}
    assert _stored_regions(db, res.json()["shipment"]["id"]) == {"east"}


def test_ac_rpl_3_external_unknown_region_is_422(client, db):
    att = _attachment(db)
    res = client.post(EXTERNAL, json=_external_body(db, att.id, regions=["north"]))
    assert res.status_code == 422, res.text


# ----------------------------------------------------------------- AC-RPL-4 validation


def test_ac_rpl_4_empty_regions_on_create_is_422(client):
    res = _staff_create(client, regions=[])
    assert res.status_code == 422, res.text


def test_ac_rpl_4_unknown_region_on_create_is_422(client):
    res = _staff_create(client, regions=["north"])
    assert res.status_code == 422, res.text


def test_ac_rpl_4_update_changes_regions_and_leaves_them_alone_when_omitted(client, db):
    created = _staff_create(client).json()
    sid = created["id"]

    res = client.put(f"{STAFF}{sid}", json={"regions": ["east"]})
    assert res.status_code == 200, res.text
    assert set(res.json()["regions"]) == {"east"}
    assert _stored_regions(db, sid) == {"east"}

    res = client.put(f"{STAFF}{sid}", json={"notes": "no regions in this payload"})
    assert res.status_code == 200, res.text
    assert _stored_regions(db, sid) == {"east"}


def test_ac_rpl_4_update_with_empty_or_unknown_regions_is_422_and_changes_nothing(client, db):
    sid = _staff_create(client, regions=["west", "east"]).json()["id"]

    ok = client.put(f"{STAFF}{sid}", json={"regions": ["east"]})
    assert ok.status_code == 200, ok.text  # the field is accepted at all

    assert client.put(f"{STAFF}{sid}", json={"regions": []}).status_code == 422
    assert client.put(f"{STAFF}{sid}", json={"regions": ["north"]}).status_code == 422
    assert _stored_regions(db, sid) == {"east"}


def test_ac_rpl_4_the_detail_response_carries_regions(client):
    sid = _staff_create(client, regions=["east"]).json()["id"]
    res = client.get(f"{STAFF}{sid}")
    assert res.status_code == 200, res.text
    assert set(res.json()["regions"]) == {"east"}


# ----------------------------------------------------------------- AC-RPL-5 list + filter


def test_ac_rpl_5_list_items_carry_regions_and_the_region_filter_matches_any(client):
    west = _staff_create(client).json()["id"]
    east = _staff_create(client, regions=["east"]).json()["id"]
    both = _staff_create(client, regions=["west", "east"]).json()["id"]

    res = client.get(STAFF, params={"limit": 100})
    assert res.status_code == 200, res.text
    by_id = {r["id"]: r for r in res.json()["data"]}
    assert set(by_id[west]["regions"]) == {"west"}
    assert set(by_id[east]["regions"]) == {"east"}
    assert set(by_id[both]["regions"]) == {"west", "east"}

    res = client.get(STAFF, params={"limit": 100, "region": "east"})
    assert res.status_code == 200, res.text
    ids = {r["id"] for r in res.json()["data"]}
    assert {east, both} <= ids
    assert west not in ids

    res = client.get(STAFF, params={"limit": 100, "region": "west"})
    ids = {r["id"] for r in res.json()["data"]}
    assert {west, both} <= ids
    assert east not in ids


# ----------------------------------------------------------------- attachment create


def _upload_client(db, monkeypatch, client, code="packing_list"):
    import app.api.v1.resources.attachments as attachments_module
    import app.services.storage_router as storage_router
    from app.services.contact_access_type_service import ContactAccessTypeService

    class _Backend:
        def upload_file(self, *, file_content, file_path, content_type=None):
            return file_path, f"https://cdn.test/{file_path}"

    monkeypatch.setattr(storage_router, "default_provider", lambda: "s3")
    monkeypatch.setattr(storage_router, "get_backend", lambda provider: _Backend())
    monkeypatch.setattr(
        storage_router, "cdn_base_url", lambda provider, key: f"https://cdn.test/{key}"
    )
    monkeypatch.setattr(attachments_module, "_create_and_send_webhook", lambda *a, **k: None)
    monkeypatch.setattr(
        ContactAccessTypeService, "get_default_access_levels", lambda self: ["dealer"]
    )
    monkeypatch.setattr(
        ContactAccessTypeService,
        "validate_access_levels",
        lambda self, levels, field_name="access_levels": list(levels),
    )
    type_id = str(uuid.uuid4())
    db.add(
        AttachmentType(
            id=type_id,
            code=code,
            type_name=unique_code("PLT")[:40],
            allowed_extensions="pdf",
            max_file_size_mb=10,
        )
    )
    db.flush()
    return type_id


def _upload(client, type_id, **form):
    return client.post(
        ATTACHMENTS,
        data={"attachment_type_id": type_id, **form},
        files={"file": (f"{unique_code('pl')}.pdf", b"%PDF-1.4 rpl", "application/pdf")},
    )


def test_attachment_create_stores_regions(client, db, monkeypatch):
    type_id = _upload_client(db, monkeypatch, client)
    res = _upload(client, type_id, regions=json.dumps(["east", "west"]))
    assert res.status_code == 201, res.text
    row = db.query(Attachment).filter(Attachment.id == res.json()["id"]).one()
    assert set(row.regions) == {"east", "west"}


def test_attachment_create_without_regions_leaves_them_null(client, db, monkeypatch):
    type_id = _upload_client(db, monkeypatch, client)
    res = _upload(client, type_id)
    assert res.status_code == 201, res.text
    row = db.query(Attachment).filter(Attachment.id == res.json()["id"]).one()
    assert row.regions is None


def test_attachment_create_with_an_invalid_region_is_422(client, db, monkeypatch):
    type_id = _upload_client(db, monkeypatch, client)
    res = _upload(client, type_id, regions=json.dumps(["north"]))
    assert res.status_code == 422, res.text
