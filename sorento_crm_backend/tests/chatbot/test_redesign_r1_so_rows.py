"""PV2 redesign R1-5: so_status / ref_status return structured rows beside their text cards.

Pinned: `SoLookup.rows`, `PoRefLookup.rows` and `RefLookup.rows` are lists of {"id": str, "number": str,
"debtor": str | None} in card order. The lane (`run_fetch`) carries them on the fixed-reply fetch as
`fetch["rows"]` = [{"kind", "key", "label"}] (kind sales_order / customer_po_ref->sales_order / purchase_order),
which `envelope_of` hands to the register writer unchanged."""
from __future__ import annotations

import uuid

import pytest

from app.models.order import SalesOrder
from app.models.procurement import PurchaseOrder
from app.services.chatbot import ref_status, so_status
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.scm.po_ref import po_ref_key
from tests.chatbot.pv2 import _resolve_seed as S


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _so(db, number, debtor="HANLIM TRADING SDN BHD", po_ref=None):
    row = SalesOrder(id=str(uuid.uuid4()), so_number=number, debtor_name=debtor, company_id=DEFAULT_COMPANY_ID,
                     po_ref=po_ref, po_ref_key=po_ref_key(po_ref) if po_ref else None)
    db.add(row)
    db.flush()
    return row


def test_so_lookup_returns_a_row_per_card_in_card_order(db):
    a, b = _so(db, "SO422057"), _so(db, "SO422056", debtor="OTHER SDN BHD")
    got = so_status.lookup(db, ["SO422057", "SO422056"], linked=None)
    assert len(got.cards) == len(got.rows) == 2
    assert got.rows == [{"id": str(a.id), "number": "SO422057", "debtor": "HANLIM TRADING SDN BHD"},
                        {"id": str(b.id), "number": "SO422056", "debtor": "OTHER SDN BHD"}]


def test_a_missing_or_refused_so_has_no_row(db):
    got = so_status.lookup(db, ["SO999999"], linked=None)
    assert got.cards == [] and got.rows == []


def test_po_ref_lookup_returns_rows_beside_its_cards(db):
    a = _so(db, "SO500001", po_ref="PO-ABC-1")
    got = so_status.lookup_po_refs(db, ["PO-ABC-1"], linked=None)
    assert got.rows == [{"id": str(a.id), "number": "SO500001", "debtor": "HANLIM TRADING SDN BHD"}]


def test_purchase_order_ref_cards_return_rows(db):
    po = PurchaseOrder(id=str(uuid.uuid4()), po_number="PO-PV2-1", status="active")
    db.add(po)
    db.flush()
    got = ref_status.lookup_purchase_orders(db, ["PO-PV2-1"])
    assert len(got.cards) == 1
    assert got.rows == [{"id": str(po.id), "number": "PO-PV2-1", "debtor": None}]
