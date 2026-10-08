"""PRODUCT-ALTERNATIVES: the stock tool attaches stored alternatives (card Q2 (b), Q4 (a)).

Card: documentation/plans/chatbot/CARD-product-alternatives-08oct.md.

Availability (dealer): an answered entry whose product is discontinued OR whose verdict is not
a full "Got stock" carries `alternatives`, each judged by the SAME rules as any other entry
(asked quantity, cap X, on hand in the contact's allowed locations, ETA). The alternative is
never a top-level entry of its own and no quantity of ours leaks through it.

Compact (staff): a block whose product is discontinued OR has 0 on hand carries
`flags.alternatives` (codes), and the alternative's own block is appended when the ask did
not already list it.

One lookup (`product_alternative_service.alternatives_for`) feeds both modes.
"""
from __future__ import annotations

from datetime import date

from app.main import app  # noqa: F401  (first app import)
from app.models.product import ProductAlternative
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.inventory_service import StockService

from tests._mc_lookup_seed import product, stock
from tests.test_stock_availability_block import (  # noqa: F401  (fixtures)
    _assert_no_quantity_anywhere,
    _attachment,
    _category_of,
    _contact,
    _entry,
    _incoming_line,
    _incoming_shipment,
    _policy_row,
    _wh,
    db,
)
from tests._pg_fixture import unique_code

X = 100  # category cap: every ask below is within it


def _pair(db, *, old_on_hand, new_on_hand, old_discontinued=True, wh=None, new_wh=None):
    wh = wh or _wh(db, unique_code("ZZAW")[:20])
    stem = unique_code("ZZALT")[:30].upper()
    old = product(db, company_id=DEFAULT_COMPANY_ID, code=stem)
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-NEW")
    old.is_discontinued = old_discontinued
    for p in (old, new):
        _category_of(db, p).chatbot_max_qty = X
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=old.id, warehouse_id=wh.id, on_hand=old_on_hand)
    stock(
        db,
        company_id=DEFAULT_COMPANY_ID,
        product_id=new.id,
        warehouse_id=(new_wh or wh).id,
        on_hand=new_on_hand,
    )
    db.add(
        ProductAlternative(
            product_id=old.id,
            alternative_product_id=new.id,
            source="auto_new",
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.flush()
    return wh, old, new


def _dealer(db, wh):
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[wh.id], contact=contact)
    db.flush()
    return contact


def _ask(db, contact, old, q):
    kwargs = {"requested_quantities": {old.id: q}} if q is not None else {}
    return StockService(db).list_stock(product_ids=[old.id], contact_id=contact.id, **kwargs)


# --------------------------------------------------------------------------- availability


def test_discontinued_no_stock_carries_the_judged_alternative(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=16)
    result = _ask(db, _dealer(db, wh), old, 5)

    entries = result["stock_availability"]
    assert [e["product_id"] for e in entries] == [old.id]  # never a top-level entry
    entry = _entry(result, old.id)
    assert entry["branch"] == "no_incoming"
    assert entry["is_discontinued"] is True
    [alt] = entry["alternatives"]
    assert alt["product_id"] == new.id
    assert alt["product_code"] == new.product_code
    assert alt["requested_qty"] == 5
    assert alt["branch"] == "in_stock"
    assert alt["available_qty"] is None
    assert alt["needs_quantity"] is False


def test_alternative_quantities_never_leak(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=16)
    result = _ask(db, _dealer(db, wh), old, 5)
    _assert_no_quantity_anywhere(result, {16, X})


def test_partial_alternative_reports_only_n(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=3)
    [alt] = _entry(_ask(db, _dealer(db, wh), old, 5), old.id)["alternatives"]
    assert alt["branch"] == "in_stock"
    assert alt["available_qty"] == 3


def test_alternative_judged_on_allowed_locations_only(db):
    hidden = _wh(db, unique_code("ZZHID")[:20])
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=500, new_wh=hidden)
    [alt] = _entry(_ask(db, _dealer(db, wh), old, 5), old.id)["alternatives"]
    assert alt["branch"] == "no_incoming"


def test_alternative_incoming_carries_its_own_eta(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=0)
    shipment = _incoming_shipment(db, eta=date(2026, 10, 20), attachment_id=_attachment(db).id)
    _incoming_line(db, shipment_id=shipment.id, product_id=new.id)
    entry = _entry(_ask(db, _dealer(db, wh), old, 2), old.id)
    assert entry["branch"] == "no_incoming"
    [alt] = entry["alternatives"]
    assert alt["branch"] == "incoming"
    assert alt["eta"] == "20/10/2026"


def test_live_old_code_out_of_stock_still_shows_alternative(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=16, old_discontinued=False)
    entry = _entry(_ask(db, _dealer(db, wh), old, 5), old.id)
    assert [a["product_id"] for a in entry["alternatives"]] == [new.id]


def test_live_old_code_partial_stock_shows_alternative(db):
    wh, old, new = _pair(db, old_on_hand=2, new_on_hand=16, old_discontinued=False)
    entry = _entry(_ask(db, _dealer(db, wh), old, 5), old.id)
    assert entry["available_qty"] == 2
    assert [a["product_id"] for a in entry["alternatives"]] == [new.id]


def test_live_old_code_full_stock_shows_no_alternative(db):
    wh, old, new = _pair(db, old_on_hand=50, new_on_hand=16, old_discontinued=False)
    entry = _entry(_ask(db, _dealer(db, wh), old, 5), old.id)
    assert entry["branch"] == "in_stock"
    assert entry.get("alternatives") == []


def test_discontinued_full_stock_still_shows_alternative(db):
    """BMC6067-shaped: discontinued with stock; Q2 (b) shows it because discontinued."""
    wh, old, new = _pair(db, old_on_hand=18, new_on_hand=0)
    entry = _entry(_ask(db, _dealer(db, wh), old, 5), old.id)
    assert entry["branch"] == "in_stock"
    [alt] = entry["alternatives"]
    assert alt["branch"] == "no_incoming"


def test_quantity_still_owed_shows_no_alternative_yet(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=16)
    entry = _entry(_ask(db, _dealer(db, wh), old, None), old.id)
    assert entry["needs_quantity"] is True
    assert entry.get("alternatives") == []


def test_inactive_alternative_is_not_offered(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=16)
    new.is_active = False
    db.flush()
    entry = _entry(_ask(db, _dealer(db, wh), old, 5), old.id)
    assert entry.get("alternatives") == []


# --------------------------------------------------------------------------- compact


def _staff(db, wh):
    contact = _contact(db)
    _policy_row(db, mode="compact", warehouse_ids=[wh.id], contact=contact)
    db.flush()
    return contact


def _summary(result, pid):
    for row in result["stock_summary"]:
        if row["product_id"] == pid:
            return row
    raise AssertionError(f"no stock_summary block for {pid}")


def test_compact_discontinued_block_names_alternative_and_lists_it(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=16)
    result = StockService(db).list_stock(product_ids=[old.id], contact_id=_staff(db, wh).id)

    assert _summary(result, old.id)["flags"]["alternatives"] == [new.product_code]
    block = _summary(result, new.id)
    assert block["total_on_hand"] == 16
    assert not block["flags"].get("alternatives")
    assert [r["product_id"] for r in result["stock_summary"]] == [old.id, new.id]


def test_compact_does_not_duplicate_an_already_listed_alternative(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=16)
    result = StockService(db).list_stock(
        product_ids=[old.id, new.id], contact_id=_staff(db, wh).id
    )
    ids = [r["product_id"] for r in result["stock_summary"]]
    assert ids.count(new.id) == 1
    assert _summary(result, old.id)["flags"]["alternatives"] == [new.product_code]


def test_compact_live_block_with_stock_has_no_alternative(db):
    wh, old, new = _pair(db, old_on_hand=7, new_on_hand=16, old_discontinued=False)
    result = StockService(db).list_stock(product_ids=[old.id], contact_id=_staff(db, wh).id)
    assert not _summary(result, old.id)["flags"].get("alternatives")
    assert [r["product_id"] for r in result["stock_summary"]] == [old.id]


def test_compact_live_block_with_zero_on_hand_names_alternative(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=16, old_discontinued=False)
    result = StockService(db).list_stock(product_ids=[old.id], contact_id=_staff(db, wh).id)
    assert _summary(result, old.id)["flags"]["alternatives"] == [new.product_code]
