"""Review round 2 gaps (REGION-PACKING-LIST, PR #1439): the semantic resolver tiers, the
draft re-upload, and the attachment listing links. Each docstring says whether the test is
red against HEAD 8634280f2 or a guard that already holds.

Postgres only, blank schema, every row seeded here.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.models.base import set_company_scope
from app.models.company import Company
from app.models.entity_attachment import EntityAttachmentLink
from app.models.access import ContactAttachmentType
from app.models.procurement import InboundShipment
from app.models.resources import AttachmentType
from app.services import entity_resolver as er
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.company_scope_resolver import apply_company_scope

from tests._mc_lookup_seed import product
from tests._pg_fixture import blank_session, unique_code
from tests.test_region_review_round1 import (  # noqa: F401  (fixtures + helpers)
    ATTACHMENTS,
    EXTERNAL,
    STAFF,
    _attachment,
    _contact,
    _external_body,
    _ship,
    _stored,
    _tag,
    client,
    db,
)
from tests.test_stock_availability_block import _incoming_shipment

MOCHA_ID = "00000000-0000-0000-0000-000000000002"
WEST = frozenset({"west"})
BOTH = frozenset({"east", "west"})
SHIP_ONLY = frozenset({"inbound_shipment"})


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


def _entity(shipment) -> er.ResolvedEntity:
    return er.ResolvedEntity(
        entity_type="inbound_shipment",
        canonical_code=shipment.shipment_number,
        uuid=str(shipment.id),
        match_field="embedding:inbound_shipment",
        match_tier="embedding",
        similarity=0.99,
        display={"semantic_match": True},
    )


def _shipment_matches(result) -> list:
    return [
        m
        for r in result.resolutions
        for m in r.matches
        if m.entity_type == "inbound_shipment"
    ]


# ------------------------------------------------------------------ 1. tier 3 + plain


def _world(db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    east_no = unique_code("EASTNO")[:40]
    west_no = unique_code("WESTNO")[:40]
    east = _ship(db, p, ["east"], number=east_no)
    west = _ship(db, p, ["west"], number=west_no)
    return east, west


def test_tier3_embedding_hit_on_an_east_only_shipment_is_dropped_for_a_west_set(db, monkeypatch):
    """Red today: `resolve_references` never drops a tier-3 shipment hit for `regions`."""
    east, west = _world(db)
    monkeypatch.setattr(er, "_tier3_embedding_lookup", lambda *a, **k: [_entity(east)])
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))

    res = er.resolve_references(db, ["zz no such code qq"], regions=WEST)
    assert _shipment_matches(res) == []


def test_tier3_embedding_hit_on_a_visible_shipment_survives(db, monkeypatch):
    """Guard: the drop must not remove a shipment the contact may see."""
    east, west = _world(db)
    monkeypatch.setattr(er, "_tier3_embedding_lookup", lambda *a, **k: [_entity(west)])
    res = er.resolve_references(db, ["zz no such code qq"], regions=WEST)
    assert [m.canonical_code for m in _shipment_matches(res)] == [west.shipment_number]


def test_tier3_embedding_hit_without_regions_is_unfiltered(db, monkeypatch):
    """Guard: no regions (staff, bare API key) leaves the semantic hit alone."""
    east, west = _world(db)
    monkeypatch.setattr(er, "_tier3_embedding_lookup", lambda *a, **k: [_entity(east)])
    res = er.resolve_references(db, ["zz no such code qq"])
    assert [m.canonical_code for m in _shipment_matches(res)] == [east.shipment_number]


def test_plain_resolution_of_an_east_only_number_returns_no_match_for_a_west_set(db):
    """Guard (kills the tier 1/2 dispatch mutation): exact tier with regions set."""
    east, west = _world(db)
    res = er.resolve_references(
        db, [east.shipment_number], regions=WEST, enable_embedding_fallback=False
    )
    assert _shipment_matches(res) == []
    both = er.resolve_references(
        db, [east.shipment_number], regions=BOTH, enable_embedding_fallback=False
    )
    assert [m.canonical_code for m in _shipment_matches(both)] == [east.shipment_number]
    plain = er.resolve_references(db, [east.shipment_number], enable_embedding_fallback=False)
    assert [m.canonical_code for m in _shipment_matches(plain)] == [east.shipment_number]


# ------------------------------------------------------------------ 4. tier 2 prefix


def test_tier2_prefix_token_of_an_east_only_number_returns_no_match_for_a_west_set(db):
    """Guard: `_tier2_fuzzy_lookup` passes regions through."""
    east, west = _world(db)
    prefix = east.shipment_number[:-2]
    unfiltered = er.resolve_references(db, [prefix], enable_embedding_fallback=False)
    assert east.shipment_number in {m.canonical_code for m in _shipment_matches(unfiltered)}

    res = er.resolve_references(db, [prefix], regions=WEST, enable_embedding_fallback=False)
    assert east.shipment_number not in {m.canonical_code for m in _shipment_matches(res)}


# ------------------------------------------------------------------ 2 + 3. RAG filters


@pytest.fixture
def db2():
    """Two companies, both in scope: a shipment number is unique per company only."""
    with blank_session() as session:
        session.add(Company(id=MOCHA_ID, name="Mocha", code=unique_code("MCH")[:20]))
        session.flush()
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
        yield session


def _shipment_in(db, company_id, number, regions):
    shipment = _incoming_shipment(db, eta=date(2026, 10, 28), company_id=company_id)
    shipment.shipment_number = number
    shipment.shipping_container_number = unique_code("CONT")[:30]
    db.flush()
    _tag(db, shipment, regions)
    return shipment


def test_rag_filter_drops_an_east_only_shipment_for_a_west_set(db, monkeypatch):
    """Guard (kills removal of the drop in `resolve_entities_to_filters`)."""
    east, west = _world(db)
    monkeypatch.setattr(er, "_rag_resolve_phrase", lambda *a, **k: [_entity(east)])
    buckets = er.resolve_entities_to_filters(
        db, ["zz nothing like it qq"], allowed_entity_types=SHIP_ONLY, regions=WEST
    )
    assert buckets.shipment_numbers == []

    open_buckets = er.resolve_entities_to_filters(
        db, ["zz nothing like it qq"], allowed_entity_types=SHIP_ONLY, regions=None
    )
    assert open_buckets.shipment_numbers == [east.shipment_number]


def test_the_hidden_shipment_drop_keys_on_the_row_not_the_number(db2, monkeypatch):
    """Red if `_drop_hidden_shipments` keys on shipment_number: the same number exists in
    two companies, East-only in one and West in the other, so the number is 'visible' and
    the East row's match survives. Keyed on uuid, only the West row's match is left (and
    the token is no longer ambiguous)."""
    number = unique_code("DUPNO")[:40]
    east = _shipment_in(db2, DEFAULT_COMPANY_ID, number, ["east"])
    west = _shipment_in(db2, MOCHA_ID, number, ["west"])
    monkeypatch.setattr(
        er, "_rag_resolve_phrase", lambda *a, **k: [_entity(east), _entity(west)]
    )

    buckets = er.resolve_entities_to_filters(
        db2, ["zz nothing like it qq"], allowed_entity_types=SHIP_ONLY, regions=WEST
    )
    assert buckets.shipment_numbers == [number]
    assert buckets.ambiguous == []


# ------------------------------------------------------------------ 5. resolve route


def _route_client(db, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(er, "_tier3_embedding_lookup", lambda *a, **k: [])
    principal = {"id": str(uuid.uuid4()), "email": "zzt-rpl-r2@test.com"}

    def _override_db():
        yield db

    async def _scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_external_api_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _scope
    return TestClient(app)


def test_resolve_route_hides_an_east_only_shipment_from_a_contact(db, monkeypatch):
    """Guard (red if the route stops passing the contact's regions)."""
    east, west = _world(db)
    contact = _contact(db)
    c = _route_client(db, monkeypatch)
    try:
        body = {
            "tokens": [east.shipment_number],
            "allowed_entity_types": ["inbound_shipment"],
        }
        with_contact = c.post(
            "/api/v1/system/references/resolve", json={**body, "contact_id": contact.id}
        )
        assert with_contact.status_code == 200, with_contact.text
        got = [
            m
            for r in with_contact.json().get("resolutions", [])
            for m in r.get("matches", [])
            if m.get("entity_type") == "inbound_shipment"
        ]
        assert got == []

        staff = c.post("/api/v1/system/references/resolve", json=body)
        assert staff.status_code == 200, staff.text
        staff_got = [
            m
            for r in staff.json().get("resolutions", [])
            for m in r.get("matches", [])
            if m.get("entity_type") == "inbound_shipment"
        ]
        assert [m["canonical_code"] for m in staff_got] == [east.shipment_number]
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------ 6. draft re-upload


@pytest.mark.parametrize("via", ["payload", "attachment"])
def test_a_matched_draft_shipment_takes_the_uploads_regions(client, db, via):
    """Red today: a proforma-convert DRAFT (regions default West) is only a placeholder
    for the real container, so the re-upload's regions must land on it. A matched
    non-draft keeps its own (round 1 test)."""
    container = f"ZZRPL{uuid.uuid4().int % 10**7:07d}"
    draft = InboundShipment(
        id=str(uuid.uuid4()),
        shipment_number=f"SHIP-DRAFT-{uuid.uuid4().hex[:8]}",
        shipping_container_number=container,
        shipment_date=date(2026, 9, 1),
        shipment_status="draft",
    )
    db.add(draft)
    db.flush()
    assert _stored(db, draft.id) == {"west"}

    att = _attachment(db, regions=["east"] if via == "attachment" else None)
    header = {"shipping_container_number": container}
    if via == "payload":
        header["regions"] = ["east"]
    res = client.post(EXTERNAL, json=_external_body(db, att.id, unique_code("REAL")[:40], **header))
    assert res.status_code == 201, res.text
    assert res.json()["shipment"]["id"] == draft.id  # it matched the draft
    assert _stored(db, draft.id) == {"east"}


# ------------------------------------------------------------------ 7. attachment listing


def _grant(db):
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
    return pl_type, contact


def _link(db, shipment, attachment):
    db.add(
        EntityAttachmentLink(
            entity_type="inbound_shipment",
            entity_id=str(shipment.id),
            attachment_id=attachment.id,
        )
    )
    db.flush()


def _names(res):
    assert res.status_code == 200, res.text
    return {r["stored_filename"] for r in res.json()["data"]}


def test_listing_hides_a_file_linked_to_an_east_only_shipment_only_via_a_link_row(client, db):
    """Red today: the listing only reads `inbound_shipments.attachment_id`, not
    `entity_attachment_links` (the Link action's table)."""
    pl_type, contact = _grant(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    east = _ship(db, p, ["east"])  # no attachment_id on the shipment itself
    west = _ship(db, p, ["west"])
    a_east = _attachment(db, type_id=pl_type.id, name="link-east.pdf")
    a_west = _attachment(db, type_id=pl_type.id, name="link-west.pdf")
    _link(db, east, a_east)
    _link(db, west, a_west)

    params = {"direct_access_only": "true", "limit": 100}
    names = _names(client.get(ATTACHMENTS, params={**params, "contact_id": contact.id}))
    assert "link-west.pdf" in names
    assert "link-east.pdf" not in names

    staff = _names(client.get(ATTACHMENTS, params={"limit": 100, "attachment_type_id": pl_type.id}))
    assert {"link-east.pdf", "link-west.pdf"} <= staff


def test_listing_for_a_contact_applies_attachment_regions_to_unlinked_files(client, db):
    """Red today for the East-tagged file: an unlinked Packing List file is decided by its
    own `attachments.regions` (NULL or West stays visible)."""
    pl_type, contact = _grant(db)
    _attachment(db, type_id=pl_type.id, name="free-east.pdf", regions=["east"])
    _attachment(db, type_id=pl_type.id, name="free-null.pdf")
    _attachment(db, type_id=pl_type.id, name="free-west.pdf", regions=["west"])
    _attachment(db, type_id=pl_type.id, name="free-both.pdf", regions=["west", "east"])

    params = {"direct_access_only": "true", "limit": 100}
    names = _names(client.get(ATTACHMENTS, params={**params, "contact_id": contact.id}))
    assert {"free-null.pdf", "free-west.pdf", "free-both.pdf"} <= names
    assert "free-east.pdf" not in names

    staff = _names(client.get(ATTACHMENTS, params={"limit": 100, "attachment_type_id": pl_type.id}))
    assert {"free-east.pdf", "free-null.pdf", "free-west.pdf", "free-both.pdf"} <= staff
