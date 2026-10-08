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
    client,
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


# --------------------------------------------------------------------------- reviewer round 1


def test_alternative_respects_the_callers_own_warehouse_narrowing(db):
    other = _wh(db, unique_code("ZZOTH")[:20])
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=500, new_wh=other)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[wh.id, other.id], contact=contact)
    db.flush()
    result = StockService(db).list_stock(
        product_ids=[old.id],
        contact_id=contact.id,
        requested_quantities={old.id: 5},
        warehouse_ids=[wh.id],
    )
    [alt] = _entry(result, old.id)["alternatives"]
    assert alt["branch"] == "no_incoming"


def test_alternative_stock_in_an_inactive_warehouse_does_not_count(db):
    closed = _wh(db, unique_code("ZZCLS")[:20])
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=500, new_wh=closed)
    closed.is_active = False
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[wh.id, closed.id], contact=contact)
    db.flush()
    [alt] = _entry(_ask(db, contact, old, 5), old.id)["alternatives"]
    assert alt["branch"] == "no_incoming"


def test_merged_code_takes_the_alternative_of_any_companys_row(db):
    from app.models.base import set_company_scope
    from tests._mc_lookup_seed import MOCHA_ID, seed_mocha, warehouse

    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    wh = _wh(db, unique_code("ZZSW")[:20])
    mocha_wh = warehouse(db, company_id=MOCHA_ID, code=unique_code("ZZMW")[:20])
    code = unique_code("ZZALTM")[:30].upper()
    sorento_old = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    mocha_old = product(db, company_id=MOCHA_ID, code=code)
    mocha_new = product(db, company_id=MOCHA_ID, code=f"{code}-NEW")
    for p in (sorento_old, mocha_old, mocha_new):
        p.is_discontinued = p is not mocha_new
        _category_of(db, p).chatbot_max_qty = X
    stock(db, company_id=MOCHA_ID, product_id=mocha_new.id, warehouse_id=mocha_wh.id, on_hand=16)
    db.add(
        ProductAlternative(
            product_id=mocha_old.id,
            alternative_product_id=mocha_new.id,
            source="auto_new",
            company_id=MOCHA_ID,
        )
    )
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[wh.id, mocha_wh.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[sorento_old.id, mocha_old.id],
        contact_id=contact.id,
        requested_quantities={sorento_old.id: 5},
    )
    [entry] = result["stock_availability"]
    assert [a["product_code"] for a in entry["alternatives"]] == [mocha_new.product_code]
    assert entry["alternatives"][0]["branch"] == "in_stock"


def test_nested_alternative_never_carries_a_packing_list(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=0)
    shipment = _incoming_shipment(db, eta=date(2026, 10, 20), attachment_id=_attachment(db).id)
    _incoming_line(db, shipment_id=shipment.id, product_id=new.id)
    contact = _contact(db, packing_list_allowed=True)
    _policy_row(db, mode="availability", warehouse_ids=[wh.id], contact=contact)
    db.flush()
    [alt] = _entry(_ask(db, contact, old, 2), old.id)["alternatives"]
    assert alt["branch"] == "incoming"
    assert alt["packing_list"] is None


def test_alternative_asked_in_its_own_right_is_not_repeated_under_the_old_code(db):
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=16)
    contact = _dealer(db, wh)
    result = StockService(db).list_stock(
        product_ids=[old.id, new.id],
        contact_id=contact.id,
        requested_quantities={old.id: 5, new.id: 5},
    )
    assert _entry(result, old.id)["alternatives"] == []
    assert _entry(result, new.id)["branch"] == "in_stock"


def test_compact_catalogue_page_does_not_append_alternative_blocks(db):
    from app.models.inventory import Stock

    wh, old, new = _pair(db, old_on_hand=5, new_on_hand=0)
    # No stock row at all: the -NEW product is not on the catalogue page by itself.
    db.query(Stock).filter(Stock.product_id == new.id).delete()
    db.flush()
    result = StockService(db).list_stock(contact_id=_staff(db, wh).id)
    ids = [r["product_id"] for r in result["stock_summary"]]
    assert old.id in ids and new.id not in ids
    assert _summary(result, old.id)["flags"]["alternatives"] == [new.product_code]


# --------------------------------------------------------------------------- owner hand test, 9 Oct


def test_owner_case_srtbf11821_x5_through_the_real_route_presenter_and_render(client, db):
    """Owner hand test round A FAIL (9 Oct 2026): dealer asks "SRTBF11821 x 5", SRTBF11821 is
    discontinued with nothing in the dealer's warehouse, SRTBF11821-NEW has 16 in BRW, the
    category cap is 100. The reply had the marker and NO Alternative line.

    Cause: `StockAvailabilityEntry` (the route's `response_model`) did not declare
    `alternatives`, so `GET /inventory/stock/balance` dropped them off the wire. The service
    tests above call `StockService` directly and never saw it. This test goes the way the
    chatbot goes: route -> MCP presenter -> `fetch.output_structurer`."""
    import json as _json
    import sys
    from pathlib import Path

    from app.services.chatbot.lanes.business import fetch

    brw = _wh(db, unique_code("ZZBRW")[:20])
    wh, old, new = _pair(db, old_on_hand=0, new_on_hand=16, wh=brw)
    contact = _dealer(db, brw)

    response = client.get(
        "/api/v1/inventory/stock/balance",
        params={
            "product_ids": old.id,
            "contact_id": contact.id,
            "space_id": "364817",
            "requested_quantities": _json.dumps({old.id: 5}),
        },
    )
    assert response.status_code == 200, response.text
    [entry] = response.json()["stock_availability"]
    assert entry["branch"] == "no_incoming"
    [alt] = entry["alternatives"]
    assert alt["product_code"] == new.product_code
    assert alt["branch"] == "in_stock"

    mcp_root = Path(__file__).resolve().parents[2] / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    from sorento_crm_mcp.presenters import present_response

    envelope = _json.loads(present_response("crm_inventory_stock_balance_list", response.text))
    said = fetch.output_structurer(envelope, {"semantic_input": {}})["response"]
    assert said == (
        f"{old.product_code} x 5: ❌ No stock and no incoming.\n"
        "⚠️  *(PRODUCT DISCONTINUED)*\n"
        f"Alternative: {new.product_code} x 5: ✅ Got stock."
    ), said


def test_owner_case_srtwc286_sh_p_partial_stock_mid_code_pair(client, db):
    """Owner hand test round A FAIL, second case (9 Oct 2026): "SRTWC286-SH-P x 5", the old
    code is discontinued with 1 on hand (partial "Only 1 got stock."), its mid-code
    alternative SRTWC286-SH-NEW-P has 23. Discontinued alone shows the line (Q2 (b))."""
    import json as _json
    import sys
    from pathlib import Path

    from app.services.chatbot.lanes.business import fetch
    from app.services.product_alternative_service import reconcile_new_alternatives

    brw = _wh(db, unique_code("ZZBRW")[:20])
    stem = unique_code("ZZWC")[:24].upper()
    old = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-SH-P")
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-SH-NEW-P")
    old.is_discontinued = True
    for p in (old, new):
        _category_of(db, p).chatbot_max_qty = X
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=old.id, warehouse_id=brw.id, on_hand=1)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=new.id, warehouse_id=brw.id, on_hand=23)
    reconcile_new_alternatives(db, new)  # the mid-code -NEW rule makes the link
    contact = _dealer(db, brw)

    response = client.get(
        "/api/v1/inventory/stock/balance",
        params={
            "product_ids": old.id,
            "contact_id": contact.id,
            "space_id": "364817",
            "requested_quantities": _json.dumps({old.id: 5}),
        },
    )
    assert response.status_code == 200, response.text

    mcp_root = Path(__file__).resolve().parents[2] / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    from sorento_crm_mcp.presenters import present_response

    envelope = _json.loads(present_response("crm_inventory_stock_balance_list", response.text))
    said = fetch.output_structurer(envelope, {"semantic_input": {}})["response"]
    assert said == (
        f"{old.product_code} x 5: \u2705 Only 1 got stock.\n"
        "\u26a0\ufe0f  *(PRODUCT DISCONTINUED)*\n"
        f"Alternative: {new.product_code} x 5: \u2705 Got stock."
    ), said


def test_the_route_schema_declares_alternatives():
    from app.schemas.inventory import StockAvailabilityEntry

    assert "alternatives" in StockAvailabilityEntry.model_fields
