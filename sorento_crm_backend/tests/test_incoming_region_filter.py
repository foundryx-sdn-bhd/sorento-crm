"""Region filter seam on the incoming-stock surface (REGION-PACKING-LIST).
AC-RPL-9 to AC-RPL-13, 15, 16 (AC-RPL-14 rides the same `/list` route).

UAC: documentation/plans/procurement/region-packing-list-acceptance-criteria.md.

A packing list (inbound shipment) carries `regions` ('west' / 'east'). A contact sees a
shipment when its regions overlap the contact's set; `east` held expands to {east, west}.
No contact in play (staff, bare API key) means no filter. Until ACCESS-MODEL lands every
contact's set is {west} (`eta_policy.contact_regions`).

Postgres only, blank schema, every row seeded here (CI's database is empty). Shipments
are tagged with raw SQL (`_tag`) so a missing column fails as a missing column, and each
assertion is scoped to the rows this test seeded.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import RespondContact
from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.eta_policy import (
    UNRESOLVED,
    ContactEtaRules,
    rules_for_contact,
)
from app.services import eta_policy
from app.services.incoming_stock_service import (
    IncomingStockService,
    earliest_packing_list_shipment,
)
from app.services.inventory_service import StockService
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import product, stock
from tests._pg_fixture import blank_session, unique_code
from tests.test_stock_availability_block import (
    _attachment,
    _category_of,
    _entry,
    _incoming_line,
    _incoming_shipment,
    _policy_row,
    _wh,
)

WEST = ["west"]
EAST = ["east"]
BOTH = ["west", "east"]
W = frozenset({"west"})
WE = frozenset({"east", "west"})


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


@pytest.fixture
def client(db, monkeypatch):
    def _override_db():
        yield db

    principal = {"id": str(uuid.uuid4()), "email": "zzt-rpl-filter@test.com"}
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

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


def _contact(db, *, packing_list_allowed=True, offset_applied=False) -> RespondContact:
    row = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT Region Contact",
        chatbot_eta_offset_applied=offset_applied,
        packing_list_allowed=packing_list_allowed,
    )
    db.add(row)
    db.flush()
    return row


def _tag(db, shipment, regions):
    """Set a shipment's regions through SQL, so a missing column reads as one."""
    db.execute(
        text("update inbound_shipments set regions = cast(:r as text[]) where id = :i"),
        {"r": "{" + ",".join(regions) + "}", "i": str(shipment.id)},
    )
    db.expire_all()


def _ship(db, p, regions, *, eta=date(2026, 10, 28), with_attachment=False):
    att = _attachment(db) if with_attachment else None
    shipment = _incoming_shipment(db, eta=eta, attachment_id=att.id if att else None)
    shipment.shipping_container_number = unique_code("CONT")[:30]
    _incoming_line(db, shipment_id=shipment.id, product_id=p.id, shipped=40)
    db.flush()
    _tag(db, shipment, regions)
    return shipment


def _world(db):
    """One product with one West, one East-only and one West+East shipment."""
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    west = _ship(db, p, WEST, eta=date(2026, 10, 10))
    east = _ship(db, p, EAST, eta=date(2026, 10, 12))
    both = _ship(db, p, BOTH, eta=date(2026, 10, 14))
    return p, west, east, both


def _numbers(rows):
    return {r["shipment_number"] for r in rows}


# ============================================================== the seam (pure)


@pytest.mark.parametrize(
    "held, expected",
    [
        (None, W),
        ([], W),
        (frozenset(), W),
        (["west"], W),
        (["east"], WE),
        (["west", "east"], WE),
        (frozenset({"east"}), WE),
    ],
)
def test_ac_rpl_9_visible_regions_expands_east_to_east_and_west(held, expected):
    assert eta_policy.visible_regions(held) == expected


def test_ac_rpl_16_contact_regions_is_west_for_any_contact(db):
    resolved = _contact(db)
    assert eta_policy.contact_regions(db, resolved.id) == W
    assert eta_policy.contact_regions(db, None) == W
    assert eta_policy.contact_regions(db, "no-such-contact") == W
    assert isinstance(eta_policy.contact_regions(db, resolved.id), frozenset)


def test_ac_rpl_11_an_unresolved_contact_gets_the_west_only_set():
    assert UNRESOLVED.regions == W


def test_ac_rpl_16_rules_for_contact_fills_regions_from_the_reader(db):
    assert rules_for_contact(db, _contact(db).id).regions == W
    assert rules_for_contact(db, None).regions == W
    assert rules_for_contact(db, "no-such-contact").regions == W
    assert "regions" in ContactEtaRules.__dataclass_fields__


# ============================================================== the service filter


def test_ac_rpl_10_west_set_hides_the_east_only_shipment_on_incoming_list(db):
    p, west, east, both = _world(db)
    res = IncomingStockService(db, regions=W).incoming_list(product_ids=[p.id])
    assert _numbers(res["data"]) == {west.shipment_number, both.shipment_number}


def test_ac_rpl_10_west_set_hides_the_east_only_shipment_on_incoming_for_product(db):
    p, west, east, both = _world(db)
    res = IncomingStockService(db, regions=W).incoming_for_product(product_ids=[p.id])
    (bucket,) = res["data"]
    assert _numbers(bucket["shipments"]) == {west.shipment_number, both.shipment_number}
    # the nearest ETA is the West one (12 Oct is East-only and 10 Oct is West, so the
    # East ETA must never be the answer when it is the earliest visible one).
    assert str(bucket["nearest_estimated_arrival_date"]) == "2026-10-10"


def test_ac_rpl_10_the_east_only_eta_never_becomes_the_nearest_eta(db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _ship(db, p, EAST, eta=date(2026, 10, 5))
    both = _ship(db, p, BOTH, eta=date(2026, 10, 20))
    res = IncomingStockService(db, regions=W).incoming_for_product(product_ids=[p.id])
    (bucket,) = res["data"]
    assert _numbers(bucket["shipments"]) == {both.shipment_number}
    assert str(bucket["nearest_estimated_arrival_date"]) == "2026-10-20"


def test_ac_rpl_10_west_set_hides_the_east_only_shipment_on_incoming_shipments(db):
    p, west, east, both = _world(db)
    res = IncomingStockService(db, regions=W).incoming_shipments(
        shipment_ids=[west.id, east.id, both.id]
    )
    assert _numbers(res["data"]) == {west.shipment_number, both.shipment_number}
    assert res["pagination"]["total"] == 2


def test_ac_rpl_10_totals_and_paging_count_only_what_is_visible(db):
    p, west, east, both = _world(db)
    svc = IncomingStockService(db, regions=W)

    page1 = svc.incoming_list(product_ids=[p.id], page=1, limit=1)
    assert page1["pagination"]["total"] == 2
    assert len(page1["data"]) == 1
    page2 = svc.incoming_list(product_ids=[p.id], page=2, limit=1)
    assert len(page2["data"]) == 1
    assert _numbers(page1["data"]) | _numbers(page2["data"]) == {
        west.shipment_number,
        both.shipment_number,
    }
    assert svc.incoming_list(product_ids=[p.id], page=3, limit=1)["data"] == []

    ship_total = svc.incoming_shipments(
        shipment_ids=[west.id, east.id, both.id], page=1, limit=1
    )["pagination"]["total"]
    assert ship_total == 2


def test_ac_rpl_10_an_east_only_product_reads_empty_on_every_list_method(db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    east = _ship(db, p, EAST)
    svc = IncomingStockService(db, regions=W)

    assert svc.incoming_list(product_ids=[p.id])["empty"] is True
    assert svc.incoming_for_product(product_ids=[p.id])["empty"] is True
    assert svc.incoming_shipments(shipment_ids=[east.id])["empty"] is True


def test_ac_rpl_10_the_single_shipment_methods_hide_an_east_only_shipment(db):
    p, west, east, both = _world(db)
    svc = IncomingStockService(db, regions=W)

    assert svc.shipment_incoming_products(east.id) == {"data": None, "empty": True}
    assert svc.shipment_attachment(east.id) is None

    visible = svc.shipment_incoming_products(west.id)
    assert visible["empty"] is False
    assert visible["data"]["products"]
    assert svc.shipment_incoming_products(both.id)["empty"] is False
    assert svc.shipment_attachment(both.id)["shipment_number"] == both.shipment_number


def test_ac_rpl_10_the_empty_result_alternatives_never_offer_an_east_only_variant(db):
    base = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZRPLALT100")
    west_variant = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZRPLALT101")
    east_variant = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZRPLALT102")
    for v in (west_variant, east_variant):
        v.variant_of_id = base.id
    db.flush()
    _ship(db, base, EAST)
    _ship(db, west_variant, WEST)
    _ship(db, east_variant, EAST)

    # Control: with no filter the probe offers both variants (proves the seed works).
    unfiltered = IncomingStockService(db).incoming_for_product(product_ids=[base.id])
    assert unfiltered["empty"] is False

    res = IncomingStockService(db, regions=W).incoming_for_product(product_ids=[base.id])
    assert res["empty"] is True
    offered = {a["value"] for a in res.get("alternatives") or []}
    assert "ZZRPLALT102" not in offered
    assert "ZZRPLALT101" in offered

    listed = IncomingStockService(db, regions=W).incoming_list(product_ids=[base.id])
    assert listed["empty"] is True
    offered = {a["value"] for a in listed.get("alternatives") or []}
    assert "ZZRPLALT102" not in offered


def test_ac_rpl_9_a_set_holding_east_sees_every_shipment(db):
    p, west, east, both = _world(db)
    res = IncomingStockService(db, regions=WE).incoming_list(product_ids=[p.id])
    assert _numbers(res["data"]) == {
        west.shipment_number,
        east.shipment_number,
        both.shipment_number,
    }


def test_ac_rpl_9_an_east_only_set_still_sees_west_shipments_through_its_expansion(db):
    p, west, east, both = _world(db)
    res = IncomingStockService(db, regions=eta_policy.visible_regions(["east"])).incoming_list(
        product_ids=[p.id]
    )
    assert len(res["data"]) == 3


def test_ac_rpl_12_no_regions_means_no_filter(db):
    p, west, east, both = _world(db)
    res = IncomingStockService(db, regions=None).incoming_list(product_ids=[p.id])
    assert len(res["data"]) == 3
    assert len(IncomingStockService(db).incoming_list(product_ids=[p.id])["data"]) == 3
    assert IncomingStockService(db).shipment_attachment(east.id) is not None


def test_ac_rpl_15_a_region_change_applies_on_the_next_call(db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    s = _ship(db, p, EAST)
    svc = IncomingStockService(db, regions=W)
    assert svc.incoming_list(product_ids=[p.id])["empty"] is True

    _tag(db, s, BOTH)
    assert _numbers(svc.incoming_list(product_ids=[p.id])["data"]) == {s.shipment_number}

    _tag(db, s, EAST)
    assert svc.incoming_list(product_ids=[p.id])["empty"] is True


# ============================================================== earliest_packing_list_shipment


def test_ac_rpl_13_earliest_picks_the_west_shipment_when_an_earlier_one_is_east_only(db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    east = _ship(db, p, EAST, eta=date(2026, 10, 1), with_attachment=True)
    west = _ship(db, p, WEST, eta=date(2026, 10, 20), with_attachment=True)

    shipment_id, eta, _att = earliest_packing_list_shipment(db, [p.id], regions=W)[p.id]
    assert shipment_id == str(west.id)
    assert eta == date(2026, 10, 20)

    # Unfiltered, the earlier East one still wins (staff / API key).
    assert earliest_packing_list_shipment(db, [p.id])[p.id][0] == str(east.id)
    assert earliest_packing_list_shipment(db, [p.id], regions=None)[p.id][0] == str(east.id)
    assert earliest_packing_list_shipment(db, [p.id], regions=WE)[p.id][0] == str(east.id)


def test_ac_rpl_13_an_east_only_container_gives_no_earliest_row_for_a_west_set(db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _ship(db, p, EAST, with_attachment=True)
    assert earliest_packing_list_shipment(db, [p.id], regions=W) == {}


def test_ac_rpl_13_a_west_plus_east_shipment_qualifies_for_a_west_set(db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    both = _ship(db, p, BOTH, with_attachment=True)
    assert earliest_packing_list_shipment(db, [p.id], regions=W)[p.id][0] == str(both.id)


# ============================================================== routes (AC-RPL-9, 10, 12, 14)

BASE = "/api/v1/incoming-stock"


def test_ac_rpl_10_list_route_with_a_contact_hides_east_only(client, db):
    p, west, east, both = _world(db)
    contact = _contact(db)
    res = client.get(
        f"{BASE}/list", params={"product_ids": p.product_code, "contact_id": contact.id}
    )
    assert res.status_code == 200, res.text
    assert _numbers(res.json()["data"]) == {west.shipment_number, both.shipment_number}
    assert res.json()["pagination"]["total"] == 2


def test_ac_rpl_12_list_route_without_a_contact_shows_every_shipment(client, db):
    p, west, east, both = _world(db)
    res = client.get(f"{BASE}/list", params={"product_ids": p.product_code})
    assert res.status_code == 200, res.text
    assert len(res.json()["data"]) == 3


def test_ac_rpl_10_by_product_route_with_a_contact_hides_east_only(client, db):
    p, west, east, both = _world(db)
    contact = _contact(db)
    res = client.get(f"{BASE}/by-product", params={"product_ids": p.id, "contact_id": contact.id})
    assert res.status_code == 200, res.text
    (bucket,) = res.json()["data"]
    assert len(bucket["shipments"]) == 2

    staff = client.get(f"{BASE}/by-product", params={"product_ids": p.id}).json()
    assert len(staff["data"][0]["shipments"]) == 3


def test_ac_rpl_10_shipments_route_with_a_contact_hides_east_only(client, db):
    p, west, east, both = _world(db)
    contact = _contact(db)
    ids = ",".join([west.id, east.id, both.id])
    res = client.get(f"{BASE}/shipments", params={"shipment_ids": ids, "contact_id": contact.id})
    assert res.status_code == 200, res.text
    assert _numbers(res.json()["data"]) == {west.shipment_number, both.shipment_number}

    staff = client.get(f"{BASE}/shipments", params={"shipment_ids": ids}).json()
    assert len(staff["data"]) == 3


def test_ac_rpl_10_single_shipment_products_route_hides_an_east_only_shipment(client, db):
    p, west, east, both = _world(db)
    contact = _contact(db)

    hidden = client.get(
        f"{BASE}/shipments/{east.id}/products", params={"contact_id": contact.id}
    )
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["empty"] is True
    assert hidden.json()["data"] is None

    shown = client.get(f"{BASE}/shipments/{both.id}/products", params={"contact_id": contact.id})
    assert shown.json()["empty"] is False

    staff = client.get(f"{BASE}/shipments/{east.id}/products")
    assert staff.json()["empty"] is False


def test_ac_rpl_10_single_shipment_attachment_route_hides_an_east_only_file(client, db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    east = _ship(db, p, EAST, with_attachment=True)
    west = _ship(db, p, WEST, with_attachment=True)
    contact = _contact(db, packing_list_allowed=True)

    hidden = client.get(
        f"{BASE}/shipments/{east.id}/attachment", params={"contact_id": contact.id}
    ).json()
    assert hidden["empty"] is True
    assert not (hidden.get("data") or {}).get("attachment")

    shown = client.get(
        f"{BASE}/shipments/{west.id}/attachment", params={"contact_id": contact.id}
    ).json()
    assert shown["data"]["attachment"]["filename"] == "packing-list.pdf"

    staff = client.get(f"{BASE}/shipments/{east.id}/attachment").json()
    assert staff["data"]["attachment"]["filename"] == "packing-list.pdf"


def test_ac_rpl_15_a_region_change_applies_on_the_next_route_call(client, db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    s = _ship(db, p, EAST)
    contact = _contact(db)
    params = {"product_ids": p.product_code, "contact_id": contact.id}

    assert client.get(f"{BASE}/list", params=params).json()["data"] == []
    _tag(db, s, BOTH)
    assert len(client.get(f"{BASE}/list", params=params).json()["data"]) == 1


# ============================================================== stock ask (AC-RPL-13)


def _stock_ask(db, p, contact, expect_branch="incoming"):
    _category_of(db, p).chatbot_max_qty = 200
    brw = _wh(db, unique_code("ZZTW")[:20])
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=0)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()
    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 150}
    )
    entry = _entry(result, p.id)
    assert entry["branch"] == expect_branch
    return entry


def test_ac_rpl_13_stock_ask_for_a_west_contact_ignores_an_earlier_east_only_shipment(db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _ship(db, p, EAST, eta=date(2026, 10, 1), with_attachment=True)
    _ship(db, p, WEST, eta=date(2026, 10, 20), with_attachment=True)

    entry = _stock_ask(db, p, _contact(db))
    assert entry["branch"] == "incoming"
    assert entry["eta"] == "20/10/2026"
    assert entry["packing_list"]


def test_ac_rpl_13_stock_ask_gives_no_eta_from_an_east_only_container(db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _ship(db, p, EAST, eta=date(2026, 10, 1), with_attachment=True)

    # An East-only container is invisible to a West-only contact: the answer is
    # "no_incoming" (saying "incoming" would leak that the East shipment exists).
    entry = _stock_ask(db, p, _contact(db), expect_branch="no_incoming")
    assert entry["eta"] is None
    assert not entry["packing_list"]


def test_ac_rpl_13_stock_ask_still_answers_from_a_west_plus_east_container(db):
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _ship(db, p, BOTH, eta=date(2026, 10, 15), with_attachment=True)

    entry = _stock_ask(db, p, _contact(db))
    assert entry["eta"] == "15/10/2026"
