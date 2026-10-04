"""AVAIL-MODE-REPLIES, tester-local pass on 7fa5d654 (step 15): a dealer's ETA ask.

* A product the dealer asked about that has no incoming shipment is still a row of the
  dealer view, with no ETA, so the reply reads "CODE: ETA not confirmed yet" (approved
  catalogue S15) instead of falling to the old "No incoming stock (ETA) found for X.
  Related products:" miss ladder, which also dropped "Couldn't find: FOO99.".
* The dates a dealer is told are dd/mm/yyyy, the format of the stock ask's own ETA line,
  never ISO.

Plan: documentation/plans/chatbot/PLAN-avail-mode-replies-02oct.md.
"""
from __future__ import annotations

from app.main import app  # noqa: F401  (first app import)
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._mc_lookup_seed import product, stock
from tests.test_incoming_contact_rules import (  # noqa: F401  (fixtures)
    PADDED,
    _contact,
    _seed,
    client,
    db,
)
from tests.test_stock_availability_block import _policy_row, _wh


def _dealer(db):
    contact = _contact(db)
    brw = _wh(db, "ZZTBRWETA")
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()
    return contact


def _ask(client, contact, *codes):
    res = client.get(
        "/api/v1/incoming-stock/list",
        params={"product_ids": ",".join(codes), "contact_id": contact.id},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_a_dealer_is_told_the_eta_as_dd_mm_yyyy(client, db):
    p, _ = _seed(db)
    contact = _dealer(db)
    body = _ask(client, contact, p.product_code)
    padded = PADDED.split("-")
    assert body["data"] == [
        {"product_code": p.product_code, "etas": [f"{padded[2]}/{padded[1]}/{padded[0]}"]}
    ]


def test_an_asked_product_with_no_shipment_is_a_row_with_no_eta(client, db):
    p, _ = _seed(db)
    bare = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=bare.id, warehouse_id=_wh(db, "ZZTBRWB").id, on_hand=0)
    contact = _dealer(db)
    body = _ask(client, contact, p.product_code, bare.product_code)
    assert [row["product_code"] for row in body["data"]] == [p.product_code, bare.product_code]
    assert body["data"][1]["etas"] == []
    assert body["empty"] is False


def test_only_a_product_with_no_shipment_is_still_answered(client, db):
    bare = product(db, company_id=DEFAULT_COMPANY_ID)
    contact = _dealer(db)
    body = _ask(client, contact, bare.product_code)
    assert body["data"] == [{"product_code": bare.product_code, "etas": []}]
    assert body["empty"] is False


def test_staff_still_get_iso_dates_and_shipment_rows(client, db):
    """Full mode is untouched: no contact, no dealer view."""
    p, _ = _seed(db)
    res = client.get("/api/v1/incoming-stock/list", params={"product_ids": p.product_code})
    assert res.status_code == 200, res.text
    row = res.json()["data"][0]
    assert "etas" not in row
    assert str(row.get("estimated_arrival_date", "")).startswith("2026-10-28")
