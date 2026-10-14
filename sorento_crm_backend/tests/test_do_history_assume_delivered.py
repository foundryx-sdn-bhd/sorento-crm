"""DO-HISTORY C2: untracked pre-cutover DOs land DELIVERED, marked, with no delivery date.

Owner answer 13 Oct: backfill from 2023-09-01; an untracked DO dated before 2026-07-01 gets status
DELIVERED and the `tracking_assumed_at` marker, and `actual_delivery_date` stays EMPTY ("they don't
have delivered date, but status is delivered"). The delivered rule accepts the marker in place of
the date.

Plan: documentation/plans/autocount/PLAN-do-history-backfill-13oct.md section 4.
UAC:  documentation/plans/autocount/do-history-backfill-13oct-acceptance-criteria.md (AC-DH30..35).
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.order import Order, OrderLine, OrderStatus
from app.services.order_service import _order_status_bucket_filter
from tests.test_ingest_autocount_do_grn import (  # noqa: F401 - env is a fixture
    _records,
    do_records,
    env,
)

CUTOVER = date(2026, 7, 1)


@pytest.fixture
def delivered(env) -> str:
    row = env.db.execute(
        select(OrderStatus).where(OrderStatus.status_code.in_(["delivered", "DELIVERED"]))
    ).scalars().first()
    if row is None:
        row = OrderStatus(status_code="delivered", status_name="Delivered")
        env.db.add(row)
        env.db.commit()
    return str(row.id)


@pytest.fixture
def cutover(monkeypatch):
    monkeypatch.setattr(settings, "do_assume_delivered_before", CUTOVER, raising=False)
    return CUTOVER


def _dated(rec: dict, day: str) -> dict:
    out = copy.deepcopy(rec)
    out["DocDate"] = f"{day}T00:00:00"
    return out


def test_setting_exists_and_ships_off():
    """AC-DH30: off unless set."""
    from app.config import Settings

    assert Settings.model_fields["do_assume_delivered_before"].default is None


def test_unset_setting_lands_new(env, delivered, monkeypatch):
    """AC-DH30."""
    monkeypatch.setattr(settings, "do_assume_delivered_before", None, raising=False)
    env.push_do([_dated(do_records()[0], "2024-03-05")])
    o = env.order(900001)
    assert str(o.order_status_id) == env.new_status
    assert o.tracking_assumed_at is None and o.actual_delivery_date is None


def test_created_before_cutover_is_delivered_marked_and_undated(env, delivered, cutover):
    """AC-DH31."""
    r = _records(env.push_do([_dated(do_records()[0], "2024-03-05")]))["db1:DO:900001"]
    assert r["outcome"] == "created"
    o = env.order(900001)
    assert str(o.order_status_id) == delivered
    assert o.tracking_assumed_at is not None
    assert o.actual_delivery_date is None


def test_created_on_or_after_cutover_is_new(env, delivered, cutover):
    """AC-DH31: the cutover day itself is not before the cutover."""
    env.push_do([_dated(do_records()[0], "2026-07-01"), _dated(do_records()[1], "2026-09-27")])
    for key in (900001, 900002):
        o = env.order(key)
        assert str(o.order_status_id) == env.new_status
        assert o.tracking_assumed_at is None


def _excel_row(env, number: str, **tracking) -> Order:
    row = Order(order_number=number, company_id=env.company, order_date=date(2024, 3, 5),
                order_status_id=tracking.pop("order_status_id", env.new_status), **tracking)
    env.db.add(row)
    env.db.flush()
    env.db.add(OrderLine(order_id=row.id, line_sequence=1, product_id=env.p1,
                         warehouse_id=env.wh1, quantity=Decimal("10")))
    env.db.commit()
    return row


def test_adopted_untracked_row_before_cutover_is_assumed(env, delivered, cutover):
    """AC-DH32."""
    row = _excel_row(env, "ZZDO-0001")
    r = _records(env.push_do([_dated(do_records()[0], "2024-03-05")]))["db1:DO:900001"]
    assert "adopted_by_doc_no" in r["warnings"]
    o = env.order(900001)
    assert str(o.id) == str(row.id)
    assert str(o.order_status_id) == delivered
    assert o.tracking_assumed_at is not None and o.actual_delivery_date is None


def test_adopted_row_with_a_date_keeps_its_tracking(env, delivered, cutover):
    """AC-DH32: any tracking wins."""
    _excel_row(env, "ZZDO-0001", actual_delivery_date=date(2024, 3, 6))
    env.push_do([_dated(do_records()[0], "2024-03-05")])
    o = env.order(900001)
    assert str(o.order_status_id) == env.new_status
    assert o.tracking_assumed_at is None
    assert o.actual_delivery_date == date(2024, 3, 6)


def test_adopted_row_with_another_status_keeps_it(env, delivered, cutover):
    """AC-DH32: a status other than new is tracking too."""
    other = OrderStatus(status_code="zz_in_transit", status_name="In transit")
    env.db.add(other)
    env.db.commit()
    _excel_row(env, "ZZDO-0001", order_status_id=str(other.id))
    env.push_do([_dated(do_records()[0], "2024-03-05")])
    o = env.order(900001)
    assert str(o.order_status_id) == str(other.id)
    assert o.tracking_assumed_at is None


def test_update_of_an_existing_do_never_assumes(env, delivered, cutover, monkeypatch):
    """AC-DH33: only the first landing assumes."""
    monkeypatch.setattr(settings, "do_assume_delivered_before", None, raising=False)
    env.push_do([_dated(do_records()[0], "2024-03-05")])
    monkeypatch.setattr(settings, "do_assume_delivered_before", CUTOVER, raising=False)
    rec = _dated(do_records()[0], "2024-03-05")
    rec["Description"] = "changed"
    rec["LastModified"] = "2026-10-01T10:00:00.000"
    assert _records(env.push_do([rec]))["db1:DO:900001"]["outcome"] == "updated"
    o = env.order(900001)
    assert str(o.order_status_id) == env.new_status
    assert o.tracking_assumed_at is None


def test_dry_run_assumes_nothing(env, delivered, cutover):
    """AC-DH34."""
    env.push_do([_dated(do_records()[0], "2024-03-05")], dry_run=True)
    assert env.db.execute(select(Order).where(Order.doc_key == 900001)).first() is None


def _bucket_ids(env, bucket: str) -> set[str]:
    clause = _order_status_bucket_filter(env.db, bucket)
    return {str(i) for (i,) in env.db.query(Order.id).filter(clause).all()}


def test_delivered_rule_accepts_the_marker_in_place_of_a_date(env, delivered):
    """AC-DH35: delivered = delivered status AND (a date OR the marker)."""
    assumed = Order(order_number="ZZDH-A", company_id=env.company, order_status_id=delivered,
                    tracking_assumed_at=datetime(2026, 10, 13, tzinfo=timezone.utc))
    dated = Order(order_number="ZZDH-D", company_id=env.company, order_status_id=delivered,
                  actual_delivery_date=date(2026, 9, 1))
    bare = Order(order_number="ZZDH-B", company_id=env.company, order_status_id=delivered)
    marked_new = Order(order_number="ZZDH-N", company_id=env.company,
                       order_status_id=env.new_status,
                       tracking_assumed_at=datetime(2026, 10, 13, tzinfo=timezone.utc))
    env.db.add_all([assumed, dated, bare, marked_new])
    env.db.commit()
    ids = {k: str(o.id) for k, o in
           {"assumed": assumed, "dated": dated, "bare": bare, "marked_new": marked_new}.items()}
    done = _bucket_ids(env, "delivered")
    pending = _bucket_ids(env, "outstanding")
    assert ids["assumed"] in done and ids["assumed"] not in pending
    assert ids["dated"] in done and ids["dated"] not in pending
    # Unchanged: a delivered status alone, or a marker under a non-delivered status, is not delivered.
    assert ids["bare"] in pending and ids["bare"] not in done
    assert ids["marked_new"] in pending and ids["marked_new"] not in done


def test_no_delivered_status_configured_assumes_nothing(env, cutover, caplog, monkeypatch):
    """AC-DH31 edge: with no `delivered` order status the DO lands new and a warning says why."""
    from app.services.autocount_doc_ingest_service import AutocountDocIngestService

    monkeypatch.setattr(AutocountDocIngestService, "_delivered_status_id", lambda self: None)
    with caplog.at_level("WARNING"):
        env.push_do([_dated(do_records()[0], "2024-03-05")])
    o = env.order(900001)
    assert str(o.order_status_id) == env.new_status
    assert o.tracking_assumed_at is None
    assert "assume_delivered_skipped" in caplog.text
