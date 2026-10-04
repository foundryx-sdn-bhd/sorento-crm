"""Review round 1 gaps (REGION-PACKING-LIST, PR #1439): reviewer + security review.

Each test says in its docstring whether it is red against the first implementation or a
guard that already holds. Postgres only, blank schema, every row seeded here.
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
from app.models.access import ContactAttachmentType, RespondContact
from app.models.base import set_company_scope
from app.models.procurement import InboundShipment
from app.models.resources import Attachment, AttachmentType
from app.services import eta_policy
from app.services import entity_resolver as er
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import product
from tests._pg_fixture import blank_session, unique_code
from tests.test_stock_availability_block import _incoming_line, _incoming_shipment

STAFF = "/api/v1/procurement/packing-lists/"
EXTERNAL = "/api/v1/external/packing-lists/"
ATTACHMENTS = "/api/v1/resource-management/attachments/"
INCOMING = "/api/v1/incoming-stock"


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


@pytest.fixture
def client(db, monkeypatch):
    def _override_db():
        yield db

    principal = {"id": str(uuid.uuid4()), "email": "zzt-rpl-r1@test.com"}
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


def _stored(db, shipment_id) -> set[str]:
    db.expire_all()
    return set(
        db.execute(
            text("select regions from inbound_shipments where id = :i"), {"i": str(shipment_id)}
        ).scalar_one()
    )


def _tag(db, shipment, regions):
    db.execute(
        text("update inbound_shipments set regions = cast(:r as text[]) where id = :i"),
        {"r": "{" + ",".join(regions) + "}", "i": str(shipment.id)},
    )
    db.expire_all()


def _contact(db, *, packing_list_allowed=True) -> RespondContact:
    row = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT R1 Contact",
        chatbot_eta_offset_applied=False,
        packing_list_allowed=packing_list_allowed,
    )
    db.add(row)
    db.flush()
    return row


def _attachment(db, *, regions=None, type_id=None, name="packing-list.pdf") -> Attachment:
    aid = str(uuid.uuid4())
    kwargs = {} if regions is None else {"regions": regions}
    row = Attachment(
        id=aid,
        original_filename=name,
        stored_filename=name,
        file_path=f"/attachments/{aid}.pdf",
        mime_type="application/pdf",
        attachment_type_id=type_id,
        **kwargs,
    )
    db.add(row)
    db.flush()
    return row


def _ship(db, p, regions, *, attachment_id=None, number=None, invoice=None, container=None):
    shipment = _incoming_shipment(db, eta=date(2026, 10, 28), attachment_id=attachment_id)
    if number:
        shipment.shipment_number = number
    shipment.invoice_number = invoice
    shipment.shipping_container_number = container or unique_code("CONT")[:30]
    _incoming_line(db, shipment_id=shipment.id, product_id=p.id, shipped=10)
    db.flush()
    _tag(db, shipment, regions)
    return shipment


# ------------------------------------------------------------------ 1. PUT regions null


def test_guard_put_null_regions_leaves_them_unchanged(client, db):
    """Guard (procurement_service.update_shipment pops a null): may already pass."""
    created = client.post(
        STAFF,
        json={
            "shipment_number": unique_code("R1")[:40],
            "shipment_date": "2026-10-01",
            "regions": ["east"],
        },
    ).json()
    res = client.put(f"{STAFF}{created['id']}", json={"regions": None})
    assert res.status_code == 200, res.text
    assert _stored(db, created["id"]) == {"east"}


# ------------------------------------------------------------------ 2 + 6. attachment create


def _upload_setup(db, monkeypatch, *, code):
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
            type_name=unique_code("T")[:40],
            allowed_extensions="pdf",
            max_file_size_mb=10,
        )
    )
    db.flush()
    return type_id


def _upload(client, type_id, name, **form):
    return client.post(
        ATTACHMENTS,
        data={"attachment_type_id": type_id, **form},
        files={"file": (name, b"%PDF-1.4 r1", "application/pdf")},
    )


def test_guard_replace_upload_of_a_packing_list_stores_the_new_regions(client, db, monkeypatch):
    """Guard (attachments.py replace branch writes regions): may already pass."""
    type_id = _upload_setup(db, monkeypatch, code="packing_list")
    name = f"{unique_code('rep')}.pdf"
    first = _upload(client, type_id, name, regions=json.dumps(["west"]))
    assert first.status_code == 201, first.text

    second = _upload(
        client, type_id, name, regions=json.dumps(["east"]), on_conflict="replace"
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    db.expire_all()
    row = db.query(Attachment).filter(Attachment.id == first.json()["id"]).one()
    assert set(row.regions) == {"east"}


def test_regions_are_stored_only_for_the_packing_list_type(client, db, monkeypatch):
    """Red today: the route stores regions for any attachment type."""
    type_id = _upload_setup(db, monkeypatch, code="certificate")
    res = _upload(client, type_id, f"{unique_code('cert')}.pdf", regions=json.dumps(["east"]))
    assert res.status_code == 201, res.text
    db.expire_all()
    row = db.query(Attachment).filter(Attachment.id == res.json()["id"]).one()
    assert row.regions is None


# ------------------------------------------------------------------ 3. matched re-upload


def _external_body(db, attachment_id, number, **header):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    return {
        "packing_list": {
            "shipment_number": number,
            "shipment_date": "2026-10-01",
            "attachment_id": attachment_id,
            **header,
        },
        "packing_list_products": [{"product_code": p.product_code, "quantity": 5}],
    }


@pytest.mark.parametrize("how", ["payload_west", "attachment_west", "nothing_stated"])
def test_a_matched_reupload_takes_the_uploads_regions(client, db, how):
    """Owner ruling 2 Oct: a re-upload onto an existing container takes the regions the
    user chose (payload, else the uploaded attachment's). An upload that states none
    leaves the container's regions alone (never nulled). Red today for the first two."""
    number = unique_code("MATCH")[:40]
    made = client.post(
        STAFF, json={"shipment_number": number, "shipment_date": "2026-10-01", "regions": ["east"]}
    )
    assert made.status_code == 201, made.text
    sid = made.json()["id"]

    att = _attachment(db, regions=["west"] if how == "attachment_west" else None)
    header = {"regions": ["west"]} if how == "payload_west" else {}
    res = client.post(EXTERNAL, json=_external_body(db, att.id, number, **header))
    assert res.status_code == 201, res.text
    assert res.json()["shipment"]["id"] == sid  # it did match the existing one
    expected = {"east"} if how == "nothing_stated" else {"west"}
    assert _stored(db, sid) == expected


# ------------------------------------------------------------------ 4. rules expansion


def test_rules_for_contact_expands_east_to_east_and_west(db, monkeypatch):
    """Red today: rules.regions is the raw reader output, not expanded."""
    monkeypatch.setattr(eta_policy, "contact_regions", lambda db_, cid: frozenset({"east"}))
    rules = eta_policy.rules_for_contact(db, _contact(db).id)
    assert rules.regions == frozenset({"east", "west"})


# ------------------------------------------------------------------ 5. ambiguous identifier


@pytest.mark.parametrize("east_first", [True, False])
def test_single_shipment_routes_resolve_an_ambiguous_ref_to_the_visible_shipment(
    client, db, east_first
):
    """Red when the East shipment is created first: the resolver picks it, then the
    region filter hides it, and the visible West shipment is never found."""
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    invoice = unique_code("INV")[:40]
    att_east = _attachment(db, name="east.pdf")
    att_west = _attachment(db, name="west.pdf")
    if east_first:
        east = _ship(db, p, ["east"], invoice=invoice, attachment_id=att_east.id)
        west = _ship(db, p, ["west"], invoice=invoice, attachment_id=att_west.id)
    else:
        west = _ship(db, p, ["west"], invoice=invoice, attachment_id=att_west.id)
        east = _ship(db, p, ["east"], invoice=invoice, attachment_id=att_east.id)
    contact = _contact(db)

    products = client.get(f"{INCOMING}/shipments/{invoice}/products", params={"contact_id": contact.id})
    assert products.status_code == 200, products.text
    assert products.json()["empty"] is False
    assert products.json()["data"]["shipment_number"] == west.shipment_number

    file = client.get(f"{INCOMING}/shipments/{invoice}/attachment", params={"contact_id": contact.id})
    assert file.status_code == 200, file.text
    assert file.json()["data"]["attachment"]["filename"] == "west.pdf"


# ------------------------------------------------------------------ 7. S1 attachment listing


def test_attachment_listing_for_a_contact_hides_files_of_east_only_shipments(client, db):
    """Red today (security S1): the list route filters by type grant only. The shipment's
    regions decide, not attachments.regions."""
    pl_type = AttachmentType(
        id=str(uuid.uuid4()),
        code="packing_list",
        type_name=unique_code("PLT")[:40],
        allowed_extensions="pdf",
        max_file_size_mb=10,
        is_direct_access=False,
    )
    db.add(pl_type)
    db.flush()
    contact = _contact(db)
    db.add(ContactAttachmentType(contact_id=contact.id, attachment_type_id=pl_type.id))
    db.flush()

    p = product(db, company_id=DEFAULT_COMPANY_ID)
    # attachments.regions deliberately disagrees with the shipment's regions.
    a_east = _attachment(db, type_id=pl_type.id, name="east-only.pdf", regions=["west"])
    a_west = _attachment(db, type_id=pl_type.id, name="west-only.pdf", regions=["east"])
    a_both = _attachment(db, type_id=pl_type.id, name="both.pdf", regions=["east"])
    _ship(db, p, ["east"], attachment_id=a_east.id)
    _ship(db, p, ["west"], attachment_id=a_west.id)
    _ship(db, p, ["west", "east"], attachment_id=a_both.id)

    params = {"direct_access_only": "true", "limit": 100}
    contact_res = client.get(ATTACHMENTS, params={**params, "contact_id": contact.id})
    assert contact_res.status_code == 200, contact_res.text
    names = {r["stored_filename"] for r in contact_res.json()["data"]}
    assert "west-only.pdf" in names and "both.pdf" in names
    assert "east-only.pdf" not in names

    staff_res = client.get(ATTACHMENTS, params={"limit": 100, "attachment_type_id": pl_type.id})
    staff_names = {r["stored_filename"] for r in staff_res.json()["data"]}
    assert {"east-only.pdf", "west-only.pdf", "both.pdf"} <= staff_names


# ------------------------------------------------------------------ 8. S2 resolver


def _resolver_world(db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    east_no = unique_code("EASTNO")[:40]
    west_no = unique_code("WESTNO")[:40]
    east_ct = unique_code("EASTCT")[:30]
    east = _ship(db, p, ["east"], number=east_no, container=east_ct)
    west = _ship(db, p, ["west"], number=west_no)
    return east, west, east_no, west_no, east_ct


def test_exact_probe_with_a_west_set_never_returns_an_east_only_shipment(db):
    """Red today (security S2): `_probe_inbound_shipment` takes no regions."""
    east, west, east_no, west_no, east_ct = _resolver_world(db)
    regions = frozenset({"west"})
    out = er._probe_inbound_shipment(db, [east_no, east_ct, west_no], regions=regions)
    assert out[east_no] == []
    assert out[east_ct] == []
    assert [m.canonical_code for m in out[west_no]] == [west_no]


def test_exact_probe_without_regions_is_unchanged(db):
    """Guard: None means no filter (staff, bare API key)."""
    east, west, east_no, west_no, east_ct = _resolver_world(db)
    out = er._probe_inbound_shipment(db, [east_no, west_no])
    assert [m.canonical_code for m in out[east_no]] == [east_no]
    assert [m.canonical_code for m in out[west_no]] == [west_no]


def test_prefix_probe_with_a_west_set_never_returns_an_east_only_shipment(db):
    """Red today (security S2): `_prefix_probe_inbound_shipment` takes no regions."""
    east, west, east_no, west_no, east_ct = _resolver_world(db)
    regions = frozenset({"west"})
    assert er._prefix_probe_inbound_shipment(db, east_no, regions=regions) == []
    assert er._prefix_probe_inbound_shipment(db, east_ct, regions=regions) == []
    got = er._prefix_probe_inbound_shipment(db, west_no, regions=regions)
    assert [m.canonical_code for m in got] == [west_no]
    both = er._prefix_probe_inbound_shipment(db, east_no, regions=frozenset({"east", "west"}))
    assert [m.canonical_code for m in both] == [east_no]
    assert [m.canonical_code for m in er._prefix_probe_inbound_shipment(db, east_no)] == [east_no]


@pytest.mark.parametrize("route", ["/shipments", "/by-product"])
def test_incoming_routes_pass_the_contacts_regions_to_the_entity_resolver(
    client, db, monkeypatch, route
):
    """Red today (security S2): the routes call `resolve_or_empty(db, norm)` with no
    regions, so an East-only shipment can be echoed in `resolved_entities`. The RAG
    resolver needs embeddings, so the seam is pinned by capturing the call: a request
    with `contact_id` must pass `regions={'west'}`; without one, no filter."""
    import app.services.entity_filter_helpers as helpers

    seen: dict = {}

    def _fake(db_, entities, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(helpers, "resolve_or_empty", _fake)
    contact = _contact(db)

    res = client.get(
        f"{INCOMING}{route}", params={"entities": "ZZ-ANY", "contact_id": contact.id}
    )
    assert res.status_code == 200, res.text
    assert seen.get("regions") == frozenset({"west"})

    seen.clear()
    res = client.get(f"{INCOMING}{route}", params={"entities": "ZZ-ANY"})
    assert res.status_code == 200, res.text
    assert seen.get("regions") is None
