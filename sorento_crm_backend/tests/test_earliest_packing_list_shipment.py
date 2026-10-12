"""earliest_packing_list_shipment (chatbot stock ask v2 S3, R5). AC-SA304 to AC-SA309,
chatbot-stock-ask-v2-24sep-acceptance-criteria.md."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.models.procurement import InboundShipment, InboundShipmentLine
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.resources import Attachment
from app.services.incoming_stock_service import earliest_packing_list_shipment
from tests._pg_fixture import blank_session


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


def _product(db, code: str) -> str:
    category_id = str(uuid.uuid4())
    uom_id = str(uuid.uuid4())
    db.add(ProductCategory(id=category_id, category_code=f"CAT-{code}", category_name=f"Cat {code}"))
    db.add(UnitOfMeasure(id=uom_id, uom_code=f"UOM-{code}", uom_name=f"Uom {code}"))
    db.flush()
    pid = str(uuid.uuid4())
    db.add(
        Product(
            id=pid,
            product_code=code,
            product_name=code,
            category_id=category_id,
            base_uom_id=uom_id,
            list_price=0,
            is_active=True,
        )
    )
    db.flush()
    return pid


def _attachment(db) -> str:
    aid = str(uuid.uuid4())
    db.add(
        Attachment(
            id=aid,
            original_filename="packing-list.pdf",
            stored_filename=f"{aid}.pdf",
            file_path=f"/attachments/{aid}.pdf",
            mime_type="application/pdf",
        )
    )
    db.flush()
    return aid


def _shipment(
    db,
    *,
    eta: date | None,
    number: str,
    attachment_id: str | None = None,
    shipment_status: str = "in_transit",
    delay: date | None = None,
) -> str:
    sid = str(uuid.uuid4())
    db.add(
        InboundShipment(
            id=sid,
            shipment_number=number,
            shipment_date=date(2026, 1, 1),
            estimated_arrival_date=eta,
            attachment_id=attachment_id,
            shipment_status=shipment_status,
            # R5 as amended by crew, 13 Oct 2026: the revised date is read when there is one.
            eta_delay_date=delay,
        )
    )
    db.flush()
    return sid


def _line(
    db,
    shipment_id: str,
    product_id: str,
    *,
    quantity_shipped: int = 10,
    quantity_received: int = 0,
    line_status: str = "in_transit",
) -> str:
    lid = str(uuid.uuid4())
    db.add(
        InboundShipmentLine(
            id=lid,
            shipment_id=shipment_id,
            product_id=product_id,
            quantity_shipped=quantity_shipped,
            quantity_received=quantity_received,
            line_status=line_status,
        )
    )
    db.flush()
    return lid


def test_ac_sa304_earliest_of_two_qualifying_shipments_wins(db):
    p = _product(db, "SA304")
    a1 = _attachment(db)
    a2 = _attachment(db)
    s_later = _shipment(db, eta=date(2026, 11, 1), number="S-LATER", attachment_id=a1)
    _line(db, s_later, p)
    s_earlier = _shipment(db, eta=date(2026, 10, 12), number="S-EARLIER", attachment_id=a2)
    _line(db, s_earlier, p)
    db.commit()

    result = earliest_packing_list_shipment(db, [p])
    shipment_id, eta, attachment_id = result[p]
    assert shipment_id == s_earlier
    assert eta == date(2026, 10, 12)
    assert attachment_id == a2


def test_ac_sa305_no_attachment_ignored_even_if_earlier(db):
    p = _product(db, "SA305A")
    a = _attachment(db)
    s_no_attachment = _shipment(db, eta=date(2026, 9, 1), number="S-NO-PL", attachment_id=None)
    _line(db, s_no_attachment, p)
    s_with_attachment = _shipment(db, eta=date(2026, 10, 1), number="S-PL", attachment_id=a)
    _line(db, s_with_attachment, p)
    db.commit()

    shipment_id, eta, _ = earliest_packing_list_shipment(db, [p])[p]
    assert shipment_id == s_with_attachment
    assert eta == date(2026, 10, 1)


def test_ac_sa305_draft_shipment_ignored(db):
    p = _product(db, "SA305B")
    a = _attachment(db)
    s_draft = _shipment(db, eta=date(2026, 1, 1), number="S-DRAFT", attachment_id=a, shipment_status="draft")
    _line(db, s_draft, p)
    db.commit()

    assert p not in earliest_packing_list_shipment(db, [p])


def test_ac_sa305_received_line_ignored(db):
    p = _product(db, "SA305C")
    a = _attachment(db)
    s = _shipment(db, eta=date(2026, 1, 1), number="S-RECEIVED", attachment_id=a)
    _line(db, s, p, quantity_received=10, line_status="received")
    db.commit()

    assert p not in earliest_packing_list_shipment(db, [p])


def test_ac_sa305_fully_received_quantity_ignored_even_if_line_status_not_flipped(db):
    p = _product(db, "SA305D")
    a = _attachment(db)
    s = _shipment(db, eta=date(2026, 1, 1), number="S-FULLY-RECV-QTY", attachment_id=a)
    _line(db, s, p, quantity_shipped=10, quantity_received=10, line_status="in_transit")
    db.commit()

    assert p not in earliest_packing_list_shipment(db, [p])


def test_ac_sa305_no_other_shipment_means_no_incoming(db):
    p = _product(db, "SA305E")
    assert p not in earliest_packing_list_shipment(db, [p])


def test_ac_sa306_the_revised_date_answers_when_there_is_one(db):
    """R5 amended (crew ruling, 13 Oct 2026, owner truthful rule): the revised ETA
    (`eta_delay_date`, `eta_policy._told`'s rule) is the date the shipment is now promised for,
    so it is the date read, compared and returned; the ETA itself only without one."""
    p = _product(db, "SA306")
    a1, a2 = _attachment(db), _attachment(db)
    delayed = _shipment(db, eta=date(2026, 10, 12), number="S-DELAYED", attachment_id=a1, delay=date(2026, 12, 1))
    _line(db, delayed, p)
    plain = _shipment(db, eta=date(2026, 11, 1), number="S-PLAIN", attachment_id=a2)
    _line(db, plain, p)
    db.commit()

    shipment_id, eta, _ = earliest_packing_list_shipment(db, [p])[p]
    assert (shipment_id, eta) == (plain, date(2026, 11, 1))


def test_ac_sa307_no_location_dimension_read_at_all(db):
    # `InboundShipmentLine` carries no warehouse column, and this read never joins
    # `spo_allocations` (the only table that names one) - so a shipment qualifies
    # at ANY location by construction, never narrowed to the asking contact's
    # policy locations the way the on-hand/open-SO reads are (R5).
    p = _product(db, "SA307")
    a = _attachment(db)
    s = _shipment(db, eta=date(2026, 10, 12), number="S-ANY-WH", attachment_id=a)
    _line(db, s, p)
    db.commit()

    result = earliest_packing_list_shipment(db, [p])
    assert result[p][0] == s


def test_ac_sa308_spo_allocations_and_po_lines_alone_produce_no_incoming(db):
    p = _product(db, "SA308")
    # No InboundShipment/InboundShipmentLine at all - only an allocation dangling
    # off nothing this read touches would count.
    assert earliest_packing_list_shipment(db, [p]) == {}


def test_ac_sa309_shipment_lacking_eta_never_qualifies(db):
    p = _product(db, "SA309")
    a = _attachment(db)
    s_no_eta = _shipment(db, eta=None, number="S-NO-ETA", attachment_id=a)
    _line(db, s_no_eta, p)
    db.commit()

    assert p not in earliest_packing_list_shipment(db, [p])


def test_ac_sa309_undated_shipment_ignored_dated_one_still_qualifies(db):
    p = _product(db, "SA309B")
    a1 = _attachment(db)
    a2 = _attachment(db)
    s_no_eta = _shipment(db, eta=None, number="S-NO-ETA-2", attachment_id=a1)
    _line(db, s_no_eta, p)
    s_dated = _shipment(db, eta=date(2026, 12, 25), number="S-DATED", attachment_id=a2)
    _line(db, s_dated, p)
    db.commit()

    shipment_id, eta, _ = earliest_packing_list_shipment(db, [p])[p]
    assert shipment_id == s_dated
    assert eta == date(2026, 12, 25)


# CHAT-FIND round 3 (crew live check, 13 Oct 2026): a dealer read "No stock, ETA 13/09/2026" a
# month after that date. A shipment whose ETA is already past is not one the dealer can be
# told is coming (R10: no "to be confirmed" answer), so `not_before` drops it; the dealer
# availability read passes today (Malaysia time). Callers that pass nothing are unchanged.


def test_not_before_drops_a_past_eta_and_keeps_the_next_one(db):
    p = _product(db, "PASTETA")
    a1, a2 = _attachment(db), _attachment(db)
    _line(db, _shipment(db, eta=date(2026, 9, 13), number="S-PAST", attachment_id=a1), p)
    s_next = _shipment(db, eta=date(2026, 11, 2), number="S-NEXT", attachment_id=a2)
    _line(db, s_next, p)
    db.commit()

    assert earliest_packing_list_shipment(db, [p])[p][1] == date(2026, 9, 13)
    shipment_id, eta, _ = earliest_packing_list_shipment(db, [p], not_before=date(2026, 10, 13))[p]
    assert (shipment_id, eta) == (s_next, date(2026, 11, 2))


def test_not_before_with_only_a_past_eta_is_no_incoming(db):
    p = _product(db, "PASTONLY")
    _line(db, _shipment(db, eta=date(2026, 9, 13), number="S-PAST-ONLY", attachment_id=_attachment(db)), p)
    db.commit()
    assert p not in earliest_packing_list_shipment(db, [p], not_before=date(2026, 10, 13))


def test_not_before_keeps_an_eta_of_today(db):
    p = _product(db, "TODAYETA")
    _line(db, _shipment(db, eta=date(2026, 10, 13), number="S-TODAY", attachment_id=_attachment(db)), p)
    db.commit()
    assert p in earliest_packing_list_shipment(db, [p], not_before=date(2026, 10, 13))


def test_the_dealer_availability_read_passes_today(monkeypatch):
    """The stock tool and the find tool share `_apply_stock_visibility`: it asks for shipments
    not before today, Malaysia time."""
    import app.services.incoming_stock_service as incoming

    seen = {}

    def _fake(db, product_ids, regions=None, not_before=None):
        seen["not_before"] = not_before
        return {}

    monkeypatch.setattr(incoming, "earliest_packing_list_shipment", _fake)
    monkeypatch.setattr(incoming, "today_myt", lambda: date(2026, 10, 13))
    from app.services.inventory_service import StockService

    assert StockService._shipments_from_today(None, ["x"], None) == {}
    assert seen["not_before"] == date(2026, 10, 13)


# Crew answer (b) then (a), 13 Oct 2026: the revised date first, then the today floor on it.


def test_a_delayed_shipment_still_coming_is_told_with_its_revised_date(db):
    p = _product(db, "DELAYFUT")
    s = _shipment(db, eta=date(2026, 9, 13), number="S-DELAY-FUT", attachment_id=_attachment(db), delay=date(2026, 11, 5))
    _line(db, s, p)
    db.commit()
    shipment_id, eta, _ = earliest_packing_list_shipment(db, [p], not_before=date(2026, 10, 13))[p]
    assert (shipment_id, eta) == (s, date(2026, 11, 5))


def test_a_past_revised_date_is_not_dated_and_the_next_shipment_answers(db):
    p = _product(db, "DELAYPAST")
    a1, a2 = _attachment(db), _attachment(db)
    _line(db, _shipment(db, eta=date(2026, 9, 1), number="S-BOTH-PAST", attachment_id=a1, delay=date(2026, 10, 1)), p)
    s_next = _shipment(db, eta=date(2026, 12, 1), number="S-NEXT-2", attachment_id=a2)
    _line(db, s_next, p)
    db.commit()
    shipment_id, eta, _ = earliest_packing_list_shipment(db, [p], not_before=date(2026, 10, 13))[p]
    assert (shipment_id, eta) == (s_next, date(2026, 12, 1))


def test_a_past_revised_date_alone_is_no_incoming(db):
    p = _product(db, "DELAYPASTONLY")
    _line(db, _shipment(db, eta=date(2026, 9, 1), number="S-PAST-2", attachment_id=_attachment(db), delay=date(2026, 10, 1)), p)
    db.commit()
    assert p not in earliest_packing_list_shipment(db, [p], not_before=date(2026, 10, 13))
