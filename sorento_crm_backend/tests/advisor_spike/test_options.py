"""C3 option sentence filling (throwaway spike). Pure: tool results are canned JSON."""
import json

from scripts.advisor_spike import options as o


def test_alternative_from_availability_entry():
    raw = json.dumps({"stock_availability": [{"product_code": "A1", "alternatives": [
        {"product_code": "A1-NEW", "branch": "in_stock"}, {"product_code": "A1-OLD", "branch": "no_incoming"}]}]})
    got = o.alternative_options(raw)
    assert [x.sentence for x in got] == ["A1-NEW is available."]


def test_alternative_none_when_not_in_stock():
    raw = json.dumps({"stock_availability": [{"alternatives": [{"product_code": "X", "branch": "no_incoming"}]}]})
    assert o.alternative_options(raw) == []


def test_incoming_staff_and_dealer_dates():
    staff = json.dumps({"data": [{"product_code": "P", "nearest_estimated_arrival_date": "2026-08-25"}]})
    dealer = json.dumps({"data": [{"product_code": "P", "etas": ["25/08/2026"]}]})
    from datetime import date

    assert o.incoming_options(staff, False, today=date(2026, 8, 1))[0].sentence == "Next arrival: 2026-08-25."
    assert o.incoming_options(dealer, True, today=date(2026, 8, 1))[0].sentence == "Next arrival for P: 25/08/2026."
    assert o.incoming_options(staff, False, today=date(2026, 10, 15))[0].sentence == "ETA on record: 2026-08-25."


def test_promo_live_only_and_end_date():
    promo = json.dumps({"data": [
        {"is_expired": True, "promotion": {"is_active": True, "description": "Old.pdf"}},
        {"is_expired": False, "promotion": {"is_active": True, "description": "SINK SALE.pdf"}}]})
    promos = json.dumps({"data": [{"description": "SINK SALE.pdf", "end_date": "2026-12-14"}]})
    assert o.promo_options(promo, promos)[0].sentence == "SINK SALE runs until 2026-12-14."


def test_accessory_uses_tool_head_only_with_rows():
    yes = json.dumps({"groups": [{"rows": [{"product_code": "SC1"}], "head": "Seat cover for WC1:"}]})
    no = json.dumps({"groups": [{"rows": [], "head": "No seat cover is on record for WC1."}]})
    assert o.accessory_options(yes)[0].sentence == "Seat cover for WC1 is SC1."
    assert o.accessory_options(no) == []
