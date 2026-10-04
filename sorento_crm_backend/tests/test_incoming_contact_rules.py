"""Issue #1328 - one per-contact ETA rule on every route, deniable container number and
quantity, and the packing list gate on the incoming routes.

Plan: `documentation/plans/chatbot/PLAN-chatbot-eta-offset-per-contact-28sep.md`.
UAC: `chatbot-eta-offset-per-contact-28sep-acceptance-criteria.md` (AC-EO1 to AC-EO9; the
dealer view is AC-EO15 and AC-EO16).

Postgres only, blank schema, every row seeded here (CI's database has none).
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import (
    AccessAgent,
    AgentFieldAccess,
    ContactAgentAccess,
    RespondContact,
)
from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.eta_policy import (
    UNRESOLVED,
    ContactEtaRules,
    rules_for_contact,
    visible_eta,
)
from app.services.field_access import (
    DEFAULT_ALLOWED,
    FIELD_LABELS,
    GATED_FIELDS,
    field_label,
)
from app.services.incoming_stock_service import CLEARANCE_KEYS
from app.services.inventory_service import StockService
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import customer, product, stock
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

OWNER = "incoming_stock_enquiries"
SHIP_ETA = date(2026, 10, 28)
# 28 Oct + 5 days crosses the month end.
PADDED = "2026-11-02"
EXACT = "2026-10-28"


def _told(iso: str) -> str:
    """The date as the dealer view tells it (AVAIL-MODE-REPLIES: dd/mm/yyyy)."""
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


@pytest.fixture
def client(db, monkeypatch):
    """Same staff-principal harness as `tests/test_stock_availability_block.py`."""

    def _override_db():
        yield db

    principal = {"id": str(uuid.uuid4()), "email": "zzt-eta-policy@test.com"}
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


def _contact(db, *, offset_applied=True, packing_list_allowed=False) -> RespondContact:
    row = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT ETA Contact",
        chatbot_eta_offset_applied=offset_applied,
        packing_list_allowed=packing_list_allowed,
    )
    db.add(row)
    db.flush()
    return row


def _seed(db, *, offset=5, eta=SHIP_ETA, delay=None):
    """One product with a category offset, one incoming shipment with a packing list,
    a container number, 40 units and a warehouse allocation-free line."""
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    category = _category_of(db, p)
    category.chatbot_max_qty = 200
    category.chatbot_eta_offset_days = offset
    att = _attachment(db)
    shipment = _incoming_shipment(db, eta=eta, attachment_id=att.id)
    shipment.shipping_container_number = unique_code("CONT")[:30]
    shipment.eta_delay_date = delay
    _incoming_line(db, shipment_id=shipment.id, product_id=p.id, shipped=40)
    db.flush()
    return p, shipment


def _list(client, p, contact=None):
    params = {"product_ids": p.product_code}
    if contact is not None:
        params["contact_id"] = contact.id
    res = client.get("/api/v1/incoming-stock/list", params=params)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["data"]) == 1, body
    return body


def _by_product(client, p, contact=None):
    params = {"product_ids": p.id}
    if contact is not None:
        params["contact_id"] = contact.id
    res = client.get("/api/v1/incoming-stock/by-product", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _shipments(client, shipment, contact=None):
    params = {"shipment_ids": shipment.id}
    if contact is not None:
        params["contact_id"] = contact.id
    res = client.get("/api/v1/incoming-stock/shipments", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _agent(db) -> AccessAgent:
    agent = db.query(AccessAgent).filter(AccessAgent.code == OWNER).first()
    if agent is None:
        agent = AccessAgent(id=str(uuid.uuid4()), code=OWNER, name="Incoming", is_active=True)
        db.add(agent)
        db.flush()
    return agent


def _grant_eta_delay(db, contact):
    """ETA delay is deny-by-default: the contact holds the agent and the field is ticked."""
    agent = _agent(db)
    db.add(
        ContactAgentAccess(
            id=str(uuid.uuid4()),
            respond_contact_id=contact.id,
            respond_contact_phone=contact.phone_number,
            agent_id=agent.id,
            is_allowed=True,
        )
    )
    _deny_on_agent(db, "eta_delay_date", allowed=True)


def _deny_on_agent(db, field_key, *, contact=None, allowed=False):
    _agent(db)
    db.add(
        AgentFieldAccess(
            id=str(uuid.uuid4()),
            agent_code=OWNER,
            resource="incoming_stock",
            field_key=field_key,
            contact_id=contact.id if contact else None,
            is_allowed=allowed,
        )
    )
    db.flush()


def _walk_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


# ============================================================== the resolver


def test_visible_eta_pads_when_the_switch_is_on():
    on = ContactEtaRules(offset_applied=True, packing_list_allowed=False)
    assert visible_eta(SHIP_ETA, 5, on) == date(2026, 11, 2)


def test_visible_eta_is_exact_when_the_switch_is_off():
    off = ContactEtaRules(offset_applied=False, packing_list_allowed=False)
    assert visible_eta(SHIP_ETA, 5, off) == SHIP_ETA


def test_visible_eta_is_exact_when_no_offset_is_set():
    on = ContactEtaRules(offset_applied=True, packing_list_allowed=False)
    assert visible_eta(SHIP_ETA, 0, on) == SHIP_ETA
    assert visible_eta(None, 5, on) is None


def test_rules_read_both_switches_off_the_contact(db):
    contact = _contact(db, offset_applied=False, packing_list_allowed=True)
    assert rules_for_contact(db, contact.id) == ContactEtaRules(
        offset_applied=False, packing_list_allowed=True
    )


def test_a_new_contact_defaults_to_the_offset_on_and_no_packing_list(db):
    row = RespondContact(
        id=unique_code("CONTACT"), phone_number="+60110000526", name="ZZT default"
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.chatbot_eta_offset_applied is True
    assert rules_for_contact(db, row.id) == ContactEtaRules(True, False)


def test_an_unresolved_contact_gets_the_padded_date_and_no_file(db):
    assert rules_for_contact(db, None) == UNRESOLVED
    assert rules_for_contact(db, "no-such-contact") == UNRESOLVED
    assert UNRESOLVED.offset_applied is True
    assert UNRESOLVED.packing_list_allowed is False


# ============================================================== stock ask


def _stock_ask_eta(db, p, contact):
    brw = _wh(db, unique_code("ZZTW")[:20])
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=0)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()
    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 150}
    )
    entry = _entry(result, p.id)
    assert entry["branch"] == "incoming"
    return entry["eta"]


def test_stock_ask_pads_the_eta_when_the_contacts_switch_is_on(db):
    p, _ = _seed(db)
    assert _stock_ask_eta(db, p, _contact(db, offset_applied=True)) == "02/11/2026"


def test_stock_ask_prints_the_exact_eta_when_the_contacts_switch_is_off(db):
    p, _ = _seed(db)
    assert _stock_ask_eta(db, p, _contact(db, offset_applied=False)) == "28/10/2026"


# ============================================================== incoming routes: ETA


def test_incoming_list_pads_eta_and_eta_delay_when_the_switch_is_on(client, db):
    p, _ = _seed(db, delay=date(2026, 11, 10))
    contact = _contact(db, offset_applied=True)
    _grant_eta_delay(db, contact)
    row = _list(client, p, contact)["data"][0]
    assert row["estimated_arrival_date"] == PADDED
    assert row["eta_delay_date"] == "2026-11-15"


def test_incoming_list_prints_the_exact_eta_when_the_switch_is_off(client, db):
    p, _ = _seed(db, delay=date(2026, 11, 10))
    contact = _contact(db, offset_applied=False)
    _grant_eta_delay(db, contact)
    row = _list(client, p, contact)["data"][0]
    assert row["estimated_arrival_date"] == EXACT
    assert row["eta_delay_date"] == "2026-11-10"


def test_incoming_list_is_exact_with_no_offset_set(client, db):
    p, _ = _seed(db, offset=None)
    row = _list(client, p, _contact(db, offset_applied=True))["data"][0]
    assert row["estimated_arrival_date"] == EXACT


def test_incoming_list_without_a_contact_is_the_exact_date(client, db):
    p, _ = _seed(db)
    row = _list(client, p)["data"][0]
    assert row["estimated_arrival_date"] == EXACT


def test_product_offset_wins_over_the_category_on_the_incoming_route(client, db):
    p, _ = _seed(db, offset=20)
    p.chatbot_eta_offset_days = 3
    db.flush()
    row = _list(client, p, _contact(db))["data"][0]
    assert row["estimated_arrival_date"] == "2026-10-31"


def test_by_product_pads_every_eta_it_prints(client, db):
    p, _ = _seed(db)
    on = _by_product(client, p, _contact(db, offset_applied=True))["data"][0]
    assert on["nearest_estimated_arrival_date"] == PADDED
    assert on["shipments"][0]["estimated_arrival_date"] == PADDED
    off = _by_product(client, p, _contact(db, offset_applied=False))["data"][0]
    assert off["nearest_estimated_arrival_date"] == EXACT
    assert off["shipments"][0]["estimated_arrival_date"] == EXACT


def test_shipments_pads_by_the_shipments_own_products(client, db):
    _p, shipment = _seed(db)
    on = _shipments(client, shipment, _contact(db, offset_applied=True))["data"][0]
    assert on["estimated_arrival_date"] == PADDED
    off = _shipments(client, shipment, _contact(db, offset_applied=False))["data"][0]
    assert off["estimated_arrival_date"] == EXACT


def test_a_mixed_shipment_row_takes_the_largest_offset(client, db):
    p, shipment = _seed(db, offset=2)
    other = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, other).chatbot_eta_offset_days = 9
    _incoming_line(db, shipment_id=shipment.id, product_id=other.id, shipped=5)
    db.flush()
    row = _shipments(client, shipment, _contact(db))["data"][0]
    assert row["estimated_arrival_date"] == "2026-11-06"


def test_stock_ask_and_incoming_agree_for_one_contact_and_product(client, db):
    """The owner's ask in one assertion: one shipment, one answer, whichever route.
    `_stock_ask_eta` puts the contact on the availability policy, so the incoming route
    answers it with the dealer view (PR #1329 fix round): the one date it tells."""
    p, _ = _seed(db)
    for switch in (True, False):
        contact = _contact(db, offset_applied=switch)
        told = _stock_ask_eta(db, p, contact)
        (listed,) = _list(client, p, contact)["data"][0]["etas"]
        # AVAIL-MODE-REPLIES: the dealer view tells dd/mm/yyyy, the stock ask's format.
        assert listed == told


# ============================================================== packing list gate


def test_incoming_list_sends_no_packing_list_to_a_contact_without_the_permission(client, db):
    """Claim (c) on main: this failed - the attachment went out to every contact."""
    p, _ = _seed(db)
    row = _list(client, p, _contact(db, packing_list_allowed=False))["data"][0]
    assert "attachment" not in row


def test_incoming_list_sends_the_packing_list_when_the_permission_is_on(client, db):
    p, _ = _seed(db)
    row = _list(client, p, _contact(db, packing_list_allowed=True))["data"][0]
    assert row["attachment"]["filename"] == "packing-list.pdf"


def test_by_product_and_shipments_gate_the_packing_list_too(client, db):
    p, shipment = _seed(db)
    denied = _contact(db, packing_list_allowed=False)
    assert "attachment" not in _by_product(client, p, denied)["data"][0]["shipments"][0]
    assert "attachment" not in _shipments(client, shipment, denied)["data"][0]
    allowed = _contact(db, packing_list_allowed=True)
    assert _by_product(client, p, allowed)["data"][0]["shipments"][0]["attachment"]
    assert _shipments(client, shipment, allowed)["data"][0]["attachment"]


def test_staff_without_a_contact_still_get_the_packing_list(client, db):
    p, _ = _seed(db)
    assert _list(client, p)["data"][0]["attachment"]["filename"] == "packing-list.pdf"


# ============================================================== container and quantity


def test_container_and_quantity_are_registered_and_ship_allowed():
    owned = GATED_FIELDS["incoming_stock"]
    for key in ("shipping_container_number", "remaining_incoming_quantity"):
        assert owned[key] == OWNER
        assert key in DEFAULT_ALLOWED
        assert key not in CLEARANCE_KEYS
    assert field_label("shipping_container_number") == "Container number"
    assert FIELD_LABELS["remaining_incoming_quantity"] == "Quantity"


def test_container_and_quantity_show_by_default(client, db):
    p, _ = _seed(db)
    row = _list(client, p, _contact(db))["data"][0]
    assert row["shipping_container_number"]
    assert row["lines"][0]["remaining_incoming_quantity"] == 40


def test_denied_on_the_agent_the_container_and_every_quantity_are_absent(client, db):
    p, shipment = _seed(db)
    _deny_on_agent(db, "shipping_container_number")
    _deny_on_agent(db, "remaining_incoming_quantity")
    dealer = _contact(db)

    body = _list(client, p, dealer)
    keys = set(_walk_keys(body["data"]))
    assert "shipping_container_number" not in keys
    assert not keys & {
        "remaining_incoming_quantity",
        "unallocated_quantity",
        "allocated_quantity",
    }
    denied = {d["field"] for d in body["field_access"]["denied"]}
    assert {"shipping_container_number", "remaining_incoming_quantity"} <= denied

    by_product_keys = set(_walk_keys(_by_product(client, p, dealer)["data"]))
    assert "shipping_container_number" not in by_product_keys
    assert "remaining_incoming_quantity" not in by_product_keys
    shipments_keys = set(_walk_keys(_shipments(client, shipment, dealer)["data"]))
    assert "shipping_container_number" not in shipments_keys
    assert "total_remaining_incoming_quantity" not in shipments_keys


def test_a_per_contact_exception_restores_them_for_that_contact(client, db):
    p, _ = _seed(db)
    _deny_on_agent(db, "shipping_container_number")
    _deny_on_agent(db, "remaining_incoming_quantity")
    staff = _contact(db)
    _deny_on_agent(db, "shipping_container_number", contact=staff, allowed=True)
    _deny_on_agent(db, "remaining_incoming_quantity", contact=staff, allowed=True)

    row = _list(client, p, staff)["data"][0]
    assert row["shipping_container_number"]
    assert row["lines"][0]["remaining_incoming_quantity"] == 40


def test_a_per_contact_deny_takes_them_from_one_contact_only(client, db):
    p, _ = _seed(db)
    dealer = _contact(db)
    _deny_on_agent(db, "shipping_container_number", contact=dealer)
    other = _contact(db)

    assert "shipping_container_number" not in _list(client, p, dealer)["data"][0]
    assert _list(client, p, other)["data"][0]["shipping_container_number"]


def test_staff_without_a_contact_keep_container_and_quantity(client, db):
    p, _ = _seed(db)
    _deny_on_agent(db, "shipping_container_number")
    _deny_on_agent(db, "remaining_incoming_quantity")
    row = _list(client, p)["data"][0]
    assert row["shipping_container_number"]
    assert row["lines"][0]["remaining_incoming_quantity"] == 40


# ============================================================== security review round 1


def test_a_revoked_eta_also_takes_the_nearest_eta_on_by_product(client, db):
    """Should-fix 1: `/by-product` states the product's nearest ETA beside each
    shipment's; a per-contact revoke of the ETA must take both."""
    p, _ = _seed(db)
    dealer = _contact(db)
    _deny_on_agent(db, "estimated_arrival_date", contact=dealer)
    keys = set(_walk_keys(_by_product(client, p, dealer)["data"]))
    assert "estimated_arrival_date" not in keys
    assert "nearest_estimated_arrival_date" not in keys


def _list_window(client, p, contact, **window):
    params = {"product_ids": p.product_code, "contact_id": contact.id, **window}
    res = client.get("/api/v1/incoming-stock/list", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_an_eta_window_is_judged_on_the_date_the_contact_is_told(client, db):
    """Should-fix 2: real ETA 28 Oct, offset 5, told 2 Nov. "Arriving by 31 Oct?" must
    not return it (that would reveal the real date), and "arriving from 1 Nov?" must,
    even though the real date is earlier than the window."""
    p, _ = _seed(db)
    padded = _contact(db, offset_applied=True)
    assert _list_window(client, p, padded, eta_to="2026-10-31")["data"] == []
    hit = _list_window(client, p, padded, eta_from="2026-11-01", eta_to="2026-11-05")
    assert [r["estimated_arrival_date"] for r in hit["data"]] == [PADDED]

    exact = _contact(db, offset_applied=False)
    assert len(_list_window(client, p, exact, eta_to="2026-10-31")["data"]) == 1
    assert _list_window(client, p, exact, eta_from="2026-11-01")["data"] == []


def test_the_by_product_window_is_judged_on_the_padded_date_too(client, db):
    p, _ = _seed(db)
    padded = _contact(db, offset_applied=True)
    res = client.get(
        "/api/v1/incoming-stock/by-product",
        params={"product_ids": p.id, "contact_id": padded.id, "eta_to": "2026-10-31"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"] == []


def test_the_single_shipment_products_route_follows_the_contact(client, db):
    """Should-fix 3: `/shipments/{id}/products` carries the container, every quantity,
    the ETA and the packing list; a contact gets them on their own rules."""
    _p, shipment = _seed(db)
    _deny_on_agent(db, "shipping_container_number")
    _deny_on_agent(db, "remaining_incoming_quantity")
    dealer = _contact(db, packing_list_allowed=False)

    res = client.get(
        f"/api/v1/incoming-stock/shipments/{shipment.id}/products",
        params={"contact_id": dealer.id},
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["estimated_arrival_date"] == PADDED
    keys = set(_walk_keys(data))
    assert not keys & {
        "attachment",
        "shipping_container_number",
        "remaining_incoming_quantity",
        "unallocated_quantity",
    }
    assert data["products"][0]["product_code"]

    staff = client.get(f"/api/v1/incoming-stock/shipments/{shipment.id}/products").json()
    assert staff["data"]["estimated_arrival_date"] == EXACT
    assert staff["data"]["attachment"]["filename"] == "packing-list.pdf"


def test_the_single_shipment_attachment_route_follows_the_packing_list_switch(client, db):
    _p, shipment = _seed(db)
    url = f"/api/v1/incoming-stock/shipments/{shipment.id}/attachment"

    denied = client.get(url, params={"contact_id": _contact(db).id}).json()
    assert "attachment" not in denied["data"]
    assert denied["empty"] is True

    allowed = client.get(
        url, params={"contact_id": _contact(db, packing_list_allowed=True).id}
    ).json()
    assert allowed["data"]["attachment"]["filename"] == "packing-list.pdf"
    assert client.get(url).json()["data"]["attachment"]["filename"] == "packing-list.pdf"


def _workspace(db, space_id: str):
    from app.models.respond_workspace import RespondWorkspace

    ws = RespondWorkspace(
        id=str(uuid.uuid4()),
        space_id=space_id,
        name=f"ZZT {space_id}",
        api_key_ciphertext="zzt-not-a-real-key",
    )
    db.add(ws)
    db.flush()
    return ws


def test_a_null_workspace_contact_is_answered_on_its_own_rules(client, db):
    """Should-fix 4: the chatbot admits a NULL-workspace contact through the fallback
    resolver; the data route resolves it the same way, so it is not read as nobody."""
    p, _ = _seed(db)
    contact = _contact(db, packing_list_allowed=True)
    contact.respond_io_id = unique_code("RIO")
    db.flush()

    res = client.get(
        "/api/v1/incoming-stock/list",
        params={
            "product_ids": p.product_code,
            "contact_id": contact.respond_io_id,
            "space_id": "364817",
        },
    )
    assert res.status_code == 200, res.text
    row = res.json()["data"][0]
    assert row["attachment"]["filename"] == "packing-list.pdf"
    assert row["shipping_container_number"]
    assert row["estimated_arrival_date"] == PADDED


def test_a_contact_in_another_workspace_fails_closed(client, db):
    """A Respond.io id paired with the wrong space_id names nobody: no packing list, and
    every gated field (the ETA included) is withheld rather than served as staff."""
    p, _ = _seed(db)
    contact = _contact(db, packing_list_allowed=True)
    contact.respond_io_id = unique_code("RIO")
    contact.workspace_id = _workspace(db, unique_code("SPACE")[:30]).id
    db.flush()

    res = client.get(
        "/api/v1/incoming-stock/list",
        params={
            "product_ids": p.product_code,
            "contact_id": contact.respond_io_id,
            "space_id": "some-other-space",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    keys = set(_walk_keys(body["data"]))
    assert not keys & {"attachment", "shipping_container_number", "estimated_arrival_date"}
    outcomes = {d["outcome"] for d in body["field_access"]["denied"]}
    assert outcomes == {"contact_not_found"}


# ============================================================== code review round 1


def _second_shipment(db, *, offset, eta):
    """Another product (own category, own offset) on its own shipment."""
    other = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, other).chatbot_eta_offset_days = offset
    shipment = _incoming_shipment(db, eta=eta)
    _incoming_line(db, shipment_id=shipment.id, product_id=other.id, shipped=5)
    db.flush()
    return other, shipment


def test_a_windowed_answer_is_paged_on_the_padded_date(client, db):
    """Blocker 1, reproduced by the reviewer: offsets 0 and 30, shipments 25 Oct and
    10 Nov, eta_from 1 Nov, limit 1. The widened SQL window's first page is the 25 Oct
    shipment, which pads out of the window - the answer must still be the 10 Nov one,
    never "nothing arriving"."""
    early_p, early = _seed(db, offset=0, eta=date(2026, 10, 25))
    late_p, late = _second_shipment(db, offset=30, eta=date(2026, 11, 10))
    # One shipment row needs its own container so `/list` can be asked by supplier-free
    # window alone: ask by both products.
    contact = _contact(db, offset_applied=True)

    res = client.get(
        "/api/v1/incoming-stock/list",
        params={
            "product_ids": f"{early_p.product_code},{late_p.product_code}",
            "contact_id": contact.id,
            "eta_from": "2026-11-01",
            "limit": 1,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [r["shipment_number"] for r in body["data"]] == [late.shipment_number]
    assert body["data"][0]["estimated_arrival_date"] == "2026-12-10"
    assert body["pagination"]["total"] == 1
    assert body["empty"] is False

    ships = client.get(
        "/api/v1/incoming-stock/shipments",
        params={
            "shipment_ids": f"{early.id},{late.id}",
            "contact_id": contact.id,
            "eta_from": "2026-11-01",
            "limit": 1,
        },
    ).json()
    assert [r["shipment_number"] for r in ships["data"]] == [late.shipment_number]
    assert ships["pagination"]["total"] == 1


def test_padded_rows_are_ordered_on_the_date_the_contact_reads(client, db):
    """Real 20 Oct + 30 = 19 Nov; real 25 Oct + 0 = 25 Oct. The service orders on the real
    date; the contact must read them in the order of the dates they are shown."""
    _p1, s1 = _seed(db, offset=30, eta=date(2026, 10, 20))
    _p2, s2 = _second_shipment(db, offset=0, eta=date(2026, 10, 25))
    rows = client.get(
        "/api/v1/incoming-stock/shipments",
        params={"shipment_ids": f"{s1.id},{s2.id}", "contact_id": _contact(db).id},
    ).json()["data"]
    assert [r["estimated_arrival_date"] for r in rows] == ["2026-10-25", "2026-11-19"]


def test_a_shipment_offset_counts_only_its_still_incoming_lines(client, db):
    """Should-fix 4: a line already received is not what `/list` answers with, so its
    product's offset must not pad the `/shipments` row either."""
    _p, shipment = _seed(db, offset=2)
    received = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, received).chatbot_eta_offset_days = 40
    _incoming_line(
        db, shipment_id=shipment.id, product_id=received.id, shipped=5, received=5,
        status="received",
    )
    db.flush()
    row = _shipments(client, shipment, _contact(db))["data"][0]
    assert row["estimated_arrival_date"] == "2026-10-30"


def test_the_stock_ask_reads_the_switch_of_a_contact_asked_by_respond_io_id(db):
    """Should-fix 3: the stock ask reads the contact's switches through the same
    `eta_policy.resolve_request_contact` the incoming routes use. (A NULL-workspace
    contact never reaches this point on a stock ask: `stock_visibility.resolve_policy`
    resolves without the fallback and answers nothing for one, before any switch is
    read - so the two resolutions cannot disagree on an answered stock ask.)"""
    p, _ = _seed(db)
    space = unique_code("SPACE")[:30]
    contact = _contact(db, offset_applied=False)
    contact.respond_io_id = unique_code("RIO")
    contact.workspace_id = _workspace(db, space).id
    db.flush()
    brw = _wh(db, unique_code("ZZTW")[:20])
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=0)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id],
        contact_id=contact.respond_io_id,
        space_id=space,
        requested_quantities={p.id: 150},
    )
    assert _entry(result, p.id)["eta"] == "28/10/2026"


# ============================================================== dealer view (PR #1329 fix round)
#
# Owner hand test, 28 Sep 2026: a contact on the "Availability only" stock policy (a
# dealer) is told the product code once, its distinct ETAs sorted, and who to ask. No
# container, no quantity, no allocation, no packing list, no repeated rows.


def _dealer(db, *, offset_applied=True, salesperson=None):
    contact = _contact(db, offset_applied=offset_applied)
    _policy_row(db, mode="availability", contact=contact)
    if salesperson is not None:
        from app.models.access import RespondContactCustomer
        from app.models.sales_agent import SalesAgent

        agent = SalesAgent(
            id=str(uuid.uuid4()), sales_agent=unique_code("ZZTSA")[:30], person_label=salesperson
        )
        db.add(agent)
        db.flush()
        cust = customer(db, company_id=DEFAULT_COMPANY_ID)
        cust.sales_agent_id = agent.id
        db.add(
            RespondContactCustomer(
                contact_id=contact.id,
                customer_id=cust.id,
                company_id=DEFAULT_COMPANY_ID,
                is_primary=True,
            )
        )
    db.flush()
    return contact


def _twin_shipment(db, p, *, eta=SHIP_ETA):
    shipment = _incoming_shipment(db, eta=eta, attachment_id=_attachment(db).id)
    shipment.shipping_container_number = unique_code("CONT")[:30]
    _incoming_line(db, shipment_id=shipment.id, product_id=p.id, shipped=25)
    db.flush()
    return shipment


def test_dealer_list_is_one_row_per_product_with_deduped_padded_etas(client, db):
    p, _ = _seed(db)
    _twin_shipment(db, p)  # same ETA, another container: the owner's duplicate
    _twin_shipment(db, p, eta=date(2026, 12, 1))
    body = _list(client, p, _dealer(db, salesperson="ZZT Sean"))
    assert body["dealer_view"] is True
    assert body["data"] == [{"product_code": p.product_code, "etas": [_told(PADDED), "06/12/2026"]}]
    # REFER-SALESMAN (30 Sep 2026): the reply never names the salesperson, so the payload
    # does not carry the name either.
    assert "salesperson_name" not in body


def test_dealer_list_carries_no_container_quantity_allocation_or_file(client, db):
    p, _ = _seed(db)
    _twin_shipment(db, p)
    body = _list(client, p, _dealer(db))
    keys = set(_walk_keys(body["data"]))
    assert keys == {"product_code", "etas"}
    assert "attachment" not in set(_walk_keys(body))


def test_dealer_list_with_the_offset_off_reads_the_exact_date(client, db):
    p, _ = _seed(db)
    body = _list(client, p, _dealer(db, offset_applied=False))
    assert body["data"][0]["etas"] == [_told(EXACT)]


def test_dealer_by_product_is_the_same_view(client, db):
    p, _ = _seed(db)
    _twin_shipment(db, p)
    body = _by_product(client, p, _dealer(db, salesperson="ZZT Sean"))
    assert body["dealer_view"] is True
    assert body["data"] == [{"product_code": p.product_code, "etas": [_told(PADDED)]}]


def test_a_non_dealer_contact_keeps_the_full_rows(client, db):
    p, _ = _seed(db)
    _twin_shipment(db, p)
    params = {"product_ids": p.product_code, "contact_id": _contact(db).id}
    body = client.get("/api/v1/incoming-stock/list", params=params).json()
    assert "dealer_view" not in body
    assert len(body["data"]) == 2
    assert all(row["lines"] for row in body["data"])


def test_staff_without_a_contact_keep_the_full_rows(client, db):
    p, _ = _seed(db)
    _twin_shipment(db, p)
    body = client.get("/api/v1/incoming-stock/list", params={"product_ids": p.product_code}).json()
    assert "dealer_view" not in body
    assert len(body["data"]) == 2
