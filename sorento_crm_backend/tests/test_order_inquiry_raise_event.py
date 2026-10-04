"""What raised each row - the actual `order_inquiry_raises` event, not the row's own
"who currently owns it" coalesce (PLAN-oi-decision-trail-ui.md, AC-DT-3).

SO390524 / OI-2609-0731 (prod, 25 Sep 2026): the row's own `raised_at` is its birth, and
the `order_inquiry_raises` event that actually raised it lands a beat later - a measured
1.3-second gap - so the match has to be a WINDOW, never an equality.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.models.project_so import OI_RAISE_RAISED, OI_RAISE_RECONFIRMED, OrderInquiryRaise

from ._pg_fixture import blank_session
from .test_order_inquiry_worklist_raised_by import (
    LIST,
    MARKER,
    READ_ONLY,
    _adopted_order,
    _client,
    _inquiry,
    _line,
    _product,
    _restore,
    _row,
    _sorento,
    _uid,
    _user,
)


def _raise(db, company_id, inquiry, *, kind, raised_by, raised_at):
    row = OrderInquiryRaise(
        id=_uid(),
        company_id=company_id,
        order_inquiry_id=inquiry.id,
        kind=kind,
        raised_by=raised_by,
        raised_at=raised_at,
    )
    db.add(row)
    db.flush()
    return row


def test_the_row_matches_the_reconfirm_event_written_a_beat_after_it():
    with blank_session() as db:
        company_id = _sorento(db)
        nurain = _user(db, f"{MARKER} Nurain", f"nurain.{_uid()[:8]}@zzt.test")
        order = _adopted_order(db, company_id, f"ZZTSO{_uid()[:8]}")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        line = _line(db, company_id, order, product)
        header = _inquiry(
            db, company_id, order, raised_by=nurain.id, raised_at=datetime(2026, 9, 20, 3, 22)
        )
        # The initial raise, well outside any row born later's own 1s window.
        _raise(
            db, company_id, header,
            kind=OI_RAISE_RAISED, raised_by=nurain.id,
            raised_at=datetime(2026, 9, 20, 3, 22),
        )
        row_created_at = datetime(2026, 9, 25, 1, 20, 33, 559000)
        # The reconfirm THIS row belongs to, written 1.3s after the row (the measured
        # prod gap) - the smallest event at or after `row.created_at - 1s`.
        matching_event_at = row_created_at + timedelta(seconds=1, microseconds=300000)
        _raise(
            db, company_id, header,
            kind=OI_RAISE_RECONFIRMED, raised_by=nurain.id,
            raised_at=matching_event_at,
        )
        # A LATER, unrelated reconfirm on the same inquiry - the match must be the
        # SMALLEST qualifying event, not merely "one that qualifies".
        _raise(
            db, company_id, header,
            kind=OI_RAISE_RECONFIRMED, raised_by=nurain.id,
            raised_at=row_created_at + timedelta(minutes=5),
        )
        row = _row(db, company_id, header, line, product.product_code)
        row.created_at = row_created_at
        db.commit()

        client, originals = _client(db, nurain.id, READ_ONLY)
        try:
            response = client.get(LIST, params={"query": product.product_code})
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        rows = {entry["id"]: entry for entry in response.json()["data"]}
        entry = rows[row.id]
        assert entry["raise_event_kind"] == OI_RAISE_RECONFIRMED
        assert entry["raise_event_by_name"] == nurain.name
        assert entry["raise_event_at"] is not None


def test_a_row_with_no_matching_event_carries_none_on_all_three():
    with blank_session() as db:
        company_id = _sorento(db)
        contact_aa = _user(db, f"{MARKER} CONTACT AA", f"CONTACT AA.{_uid()[:8]}@zzt.test")
        order = _adopted_order(db, company_id, f"ZZTSO{_uid()[:8]}")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        line = _line(db, company_id, order, product)
        header = _inquiry(
            db, company_id, order, raised_by=contact_aa.id,
            raised_at=datetime(2026, 8, 1, 0, 0),
        )
        # An event that exists, but is far outside this row's own window.
        _raise(
            db, company_id, header,
            kind=OI_RAISE_RAISED, raised_by=contact_aa.id,
            raised_at=datetime(2026, 8, 1, 0, 0),
        )
        row = _row(db, company_id, header, line, product.product_code)
        row.created_at = datetime(2026, 9, 1, 0, 0)
        db.commit()

        client, originals = _client(db, contact_aa.id, READ_ONLY)
        try:
            response = client.get(LIST, params={"query": product.product_code})
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        rows = {entry["id"]: entry for entry in response.json()["data"]}
        entry = rows[row.id]
        assert entry["raise_event_kind"] is None
        assert entry["raise_event_by_name"] is None
        assert entry["raise_event_at"] is None
        # The pre-existing "who currently owns this row" column is untouched by this
        # change - it still reads off its own coalesce, never off `order_inquiry_raises`.
        assert entry["raised_by_name"] == contact_aa.name


def test_a_row_whose_only_event_is_hours_later_carries_none_on_all_three():
    """Reviewer B1, round 1. On the 24 Sep prod copy 2,070 sheet-migrated rows had no
    event of their own and latched onto a reconfirm 1 to 23 hours later, reading
    "Reconfirmed by Jayson Foundryx" for a row a spreadsheet raised. Measured gaps for a
    REAL match: 10,851 within 1.8s, 85 between 2s and 67s, then nothing until 1h 14m - so
    the window has an upper bound of 10 minutes and anything past it is not this row's."""
    with blank_session() as db:
        company_id = _sorento(db)
        jayson = _user(db, f"{MARKER} Jayson", f"jayson.{_uid()[:8]}@zzt.test")
        order = _adopted_order(db, company_id, f"ZZTSO{_uid()[:8]}")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        line = _line(db, company_id, order, product)
        header = _inquiry(
            db, company_id, order, raised_by=jayson.id,
            raised_at=datetime(2026, 9, 1, 0, 0),
        )
        row = _row(db, company_id, header, line, product.product_code)
        row.created_at = datetime(2026, 9, 1, 0, 0)
        # The ONLY event on this inquiry, hours after the row was born.
        _raise(
            db, company_id, header,
            kind=OI_RAISE_RECONFIRMED, raised_by=jayson.id,
            raised_at=row.created_at + timedelta(hours=3),
        )
        db.commit()

        client, originals = _client(db, jayson.id, READ_ONLY)
        try:
            response = client.get(LIST, params={"query": product.product_code})
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        rows = {entry["id"]: entry for entry in response.json()["data"]}
        entry = rows[row.id]
        assert entry["raise_event_kind"] is None
        assert entry["raise_event_by_name"] is None
        assert entry["raise_event_at"] is None


def test_an_event_a_minute_after_the_row_still_matches():
    """The 85 real matches between 2s and 67s sit INSIDE the 10-minute bound."""
    with blank_session() as db:
        company_id = _sorento(db)
        nurain = _user(db, f"{MARKER} Nurain", f"nurain.{_uid()[:8]}@zzt.test")
        order = _adopted_order(db, company_id, f"ZZTSO{_uid()[:8]}")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        line = _line(db, company_id, order, product)
        header = _inquiry(
            db, company_id, order, raised_by=nurain.id,
            raised_at=datetime(2026, 9, 1, 0, 0),
        )
        row = _row(db, company_id, header, line, product.product_code)
        row.created_at = datetime(2026, 9, 1, 0, 0)
        _raise(
            db, company_id, header,
            kind=OI_RAISE_RECONFIRMED, raised_by=nurain.id,
            raised_at=row.created_at + timedelta(seconds=67),
        )
        db.commit()

        client, originals = _client(db, nurain.id, READ_ONLY)
        try:
            response = client.get(LIST, params={"query": product.product_code})
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        entry = {e["id"]: e for e in response.json()["data"]}[row.id]
        assert entry["raise_event_kind"] == OI_RAISE_RECONFIRMED
        assert entry["raise_event_by_name"] == nurain.name
