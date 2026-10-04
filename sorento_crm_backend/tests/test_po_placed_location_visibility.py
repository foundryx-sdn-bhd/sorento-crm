"""STOCK-TOTAL-OS-SCOPE tester finding: the zero-stock reply's on-order note (the
`crm_procurement_po_placed_list` rung) printed `*Location:* BRW-NTC` for a contact whose
stock-visibility policy hides BRW-NTC. Same rule as the stock reply: a hidden warehouse is
never shown or hinted.

Under a `contact_id`, `GET /procurement/purchase-orders/placed` omits `location` on every
row whose location the contact may not see (the same `warehouse_criterion` + active
filter the stock lines use). The row itself stays: what is on order is still the answer,
only the location is withheld. `availability` mode names no location at all, and an
unresolvable contact fails closed. No `contact_id` (staff, n8n) is unchanged.

Postgres only, blank schema, every row seeded here (CI's database has none).
"""
from __future__ import annotations

import uuid

from app.models.access import RespondContact, StockVisibilityPolicy
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._mc_lookup_seed import product
from tests._pg_fixture import unique_code
from tests.test_purchase_orders_placed import (  # noqa: F401 - fixtures
    BASE,
    _po_line,
    _spo,
    _warehouse,
    client,
    db,
)


def _contact(db) -> RespondContact:
    row = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT Contact",
    )
    db.add(row)
    db.flush()
    return row


def _policy(db, contact, *, mode, warehouse_ids=None, excluded_warehouse_ids=None):
    db.add(
        StockVisibilityPolicy(
            id=str(uuid.uuid4()),
            contact_id=contact.id,
            mode=mode,
            warehouse_ids=warehouse_ids,
            excluded_warehouse_ids=excluded_warehouse_ids,
        )
    )
    db.flush()


def _seed(db):
    """PO lines on visible VIS and hidden HID, an SPO allocation on HID by location code
    only, and a PO line with no warehouse."""
    vis = _warehouse(db, code=unique_code("VIS"))
    hid = _warehouse(db, code=unique_code("BRW-NTC"))
    prod = product(db, company_id=DEFAULT_COMPANY_ID)
    _po_line(db, product_id=prod.id, ordered=10, received=0, po_number="PO-VIS", warehouse_id=vis.id)
    _po_line(db, product_id=prod.id, ordered=7, received=0, po_number="PO-HID", warehouse_id=hid.id)
    _po_line(db, product_id=prod.id, ordered=5, received=0, po_number="PO-NONE")
    _spo(db, product_id=prod.id, allocated=4, number="SPO-HID", location_code=hid.warehouse_code)
    db.flush()
    return vis, hid, prod


def _rows(client, prod, **params):
    resp = client.get(f"{BASE}/placed", params={"product_ids": prod.id, **params})
    assert resp.status_code == 200, resp.text
    return resp, {r["po_number"]: r for r in resp.json()["data"]}


def test_restricted_contact_never_sees_a_hidden_location(client, db):
    vis, hid, prod = _seed(db)
    contact = _contact(db)
    _policy(db, contact, mode="compact", warehouse_ids=[vis.id])

    resp, rows = _rows(client, prod, contact_id=contact.id)

    assert rows["PO-VIS"]["location"] == vis.warehouse_code
    # The on-order rows stay; only the hidden location is withheld.
    assert rows["PO-HID"]["location"] is None
    assert rows["SPO-HID"]["location"] is None
    assert rows["PO-NONE"]["location"] is None
    assert hid.warehouse_code not in resp.text


def test_exclusion_policy_withholds_the_excluded_location(client, db):
    vis, hid, prod = _seed(db)
    contact = _contact(db)
    _policy(db, contact, mode="detailed", excluded_warehouse_ids=[hid.id])

    resp, rows = _rows(client, prod, contact_id=contact.id)

    assert rows["PO-VIS"]["location"] == vis.warehouse_code
    assert rows["PO-HID"]["location"] is None
    assert hid.warehouse_code not in resp.text


def test_every_warehouse_policy_keeps_every_location(client, db):
    vis, hid, prod = _seed(db)
    contact = _contact(db)
    _policy(db, contact, mode="compact")

    _resp, rows = _rows(client, prod, contact_id=contact.id)

    assert rows["PO-VIS"]["location"] == vis.warehouse_code
    assert rows["PO-HID"]["location"] == hid.warehouse_code
    assert rows["SPO-HID"]["location"] == hid.warehouse_code


def test_availability_mode_names_no_location(client, db):
    vis, hid, prod = _seed(db)
    contact = _contact(db)
    _policy(db, contact, mode="availability", warehouse_ids=[vis.id, hid.id])

    _resp, rows = _rows(client, prod, contact_id=contact.id)

    assert {r["location"] for r in rows.values()} == {None}


def test_unresolvable_contact_fails_closed(client, db):
    _vis, _hid, prod = _seed(db)

    _resp, rows = _rows(client, prod, contact_id="no-such-contact")

    assert {r["location"] for r in rows.values()} == {None}


def test_staff_call_without_contact_is_unchanged(client, db):
    vis, hid, prod = _seed(db)

    _resp, rows = _rows(client, prod)

    assert rows["PO-VIS"]["location"] == vis.warehouse_code
    assert rows["PO-HID"]["location"] == hid.warehouse_code
    assert rows["SPO-HID"]["location"] == hid.warehouse_code
