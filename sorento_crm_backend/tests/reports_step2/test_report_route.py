"""Phase 2 RED tests - `GET /api/v1/order-management/report` (REPORTS-STEP2 S1, AC-1..AC-7, AC-10).

Same seeded world and fixtures as `tests/test_report_ask_route.py`. Postgres only; every row seeded here.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests._sales_report_do_seed import client, db  # noqa: F401  (fixtures)
from tests.test_report_ask_route import (
    KEY,
    PERIOD,
    _as_contact,
    _code,
    _dealer,
    _full,
    _ranking_world,
)

D = Decimal
ROUTE = "/api/v1/order-management/report"
ASK = "/api/v1/order-management/report-ask"
S1 = (
    "ordered_amount", "ordered_qty", "delivered_amount", "delivered_qty",
    "outstanding_amount", "outstanding_qty",
)


def _get(client, path, contact, headers=KEY, **params):
    q = {**PERIOD, **_as_contact(contact), **params}
    return client.get(path, params={k: v for k, v in q.items() if v is not None}, headers=headers)


def _report(client, contact, **params):
    return _get(client, ROUTE, contact, **params)


def _ask(client, contact, **params):
    return _get(client, ASK, contact, **params)


# ------------------------------------------------------------------ AC-1 equivalence


def _open_lines(db, w):
    """Partly delivered September lines, so the outstanding basis has rows on every ranked axis
    (the ranking world is fully delivered, which leaves nothing outstanding)."""
    from tests._report_ask_so_seed import seed_so_line

    for agent, prod, qty, total in ((w.a1, w.p1, 10, "100.00"), (w.a2, w.p2, 8, "80.00"), (w.a3, w.p3, 6, "60.00")):
        seed_so_line(db, cust=w.cust, prod=prod, qty=qty, total=total, agent=agent, wh=w.wh, delivered=qty // 2)
    db.commit()


@pytest.mark.parametrize("key", S1)
@pytest.mark.parametrize("shape", [{"group_by": "sales_agent", "top_n": 10}, {"group_by": "product", "top_n": 2, "sort": "asc"}, {}])
def test_ac1_report_equals_report_ask_for_the_same_inputs(client, db, key, shape):
    w = _ranking_world(db)
    _open_lines(db, w)
    contact = _full(db)
    basis, figure = key.split("_")
    new = _report(client, contact, measure=key, **shape)
    old = _ask(client, contact, basis=basis, measure=figure, **shape)
    assert old.status_code == 200, old.text
    assert new.status_code == 200, new.text
    n, o = new.json(), old.json()
    assert o["rows"] or not shape, o
    assert n["rows"] == o["rows"]  # includes each row's `keys`
    for field in ("total", "total_count", "more", "status", "filters", "date_from", "date_to", "group_by"):
        assert n[field] == o[field], field


# ------------------------------------------------------------------ AC-2


def test_ac2_unknown_measure_is_422_listing_the_valid_keys(client, db):
    contact = _full(db)
    resp = _report(client, contact, measure="zzt_nope")
    assert resp.status_code == 422, resp.text
    for key in S1:
        assert key in resp.text, resp.text


def test_ac2_missing_measure_is_422(client, db):
    resp = _report(client, _full(db))
    assert resp.status_code == 422, resp.text
    assert "required" in resp.text.lower() and "Unknown measure" not in resp.text, resp.text


# ------------------------------------------------------------------ AC-3


def test_ac3_the_period_is_required(client, db):
    resp = _report(client, _full(db), measure="delivered_amount", date_from=None, date_to=None)
    assert resp.status_code == 422 and _code(resp) == "period_required", resp.text


def test_ac3_group_by_needs_top_n(client, db):
    resp = _report(client, _full(db), measure="delivered_amount", group_by="product")
    assert resp.status_code == 422 and _code(resp) == "top_n_required", resp.text


def test_ac3_an_empty_filter_is_422(client, db):
    resp = _report(client, _full(db), measure="delivered_amount", brand_ids=[" "])
    assert resp.status_code == 422 and _code(resp) == "empty_filter", resp.text


# ------------------------------------------------------------------ AC-4


def test_ac4_a_contact_without_the_grant_is_refused_like_report_ask(client, db):
    import uuid

    params = {**PERIOD, "contact_id": str(uuid.uuid4()), "space_id": "zzt-space", "measure": "delivered_amount"}
    resp = client.get(ROUTE, params=params, headers=KEY)
    assert resp.status_code == 403 and _code(resp) == "sales_report_not_enabled", resp.text


def test_ac4_a_call_without_the_api_key_is_refused(client, db):
    resp = _report(client, _full(db), headers={}, measure="delivered_amount")
    assert resp.status_code in (401, 403), resp.text
    assert resp.status_code != 404


# ------------------------------------------------------------------ AC-5


@pytest.mark.parametrize("group_by", ["sales_agent", "location", "customer", "channel"])
def test_ac5_a_dealer_may_not_group_by_staff_dimensions(client, db, group_by):
    w = _ranking_world(db)
    resp = _report(client, _dealer(db, w), measure="delivered_amount", group_by=group_by, top_n=3)
    assert resp.status_code == 403 and _code(resp) == "report_dimension_not_allowed", resp.text


@pytest.mark.parametrize("group_by", ["product", "brand", "category", "month"])
def test_ac5_a_dealer_may_group_by_the_dealer_dimensions(client, db, group_by):
    w = _ranking_world(db)
    resp = _report(client, _dealer(db, w), measure="delivered_amount", group_by=group_by, top_n=3)
    assert resp.status_code == 200, resp.text


def test_ac5_a_dealer_total_is_its_own_customers_only(client, db):
    w = _ranking_world(db)
    from tests._report_ask_so_seed import seed_so_line
    from tests._sales_report_do_seed import seed_customer
    from tests._pg_fixture import unique_code

    other = seed_customer(db, name=unique_code("OtherCust"))
    seed_so_line(db, cust=other, prod=w.p1, qty=99, total="9999.00", agent=w.a1, wh=w.wh)
    db.commit()
    body = _report(client, _dealer(db, w), measure="delivered_amount").json()
    assert body["total"]["qty"] == 70 and D(str(body["total"]["amount"])) == D("5250.00"), body


# ------------------------------------------------------------------ AC-6 / AC-7 (fake measure)


def test_ac7_a_registered_fake_measure_is_served_with_no_other_change(client, db, fake_measure):
    resp = _report(client, _full(db), measure="zzt_fake_measure")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"]["qty"] == 7 and body["total"]["amount"] == 12.5, body
    assert len(fake_measure.calls) == 1


def test_ac7_the_422_for_an_unknown_measure_lists_the_fake_too(client, db, fake_measure):
    resp = _report(client, _full(db), measure="zzt_nope")
    assert resp.status_code == 422 and "zzt_fake_measure" in resp.text, resp.text


def test_ac6_a_group_by_outside_the_measures_dimensions_is_422(client, db, fake_measure):
    resp = _report(client, _full(db), measure="zzt_fake_measure", group_by="customer", top_n=3)
    assert resp.status_code == 422, resp.text
    assert fake_measure.calls == []


def test_ac6_a_group_by_inside_the_measures_dimensions_is_served(client, db, fake_measure):
    resp = _report(client, _full(db), measure="zzt_fake_measure", group_by="product", top_n=3)
    assert resp.status_code == 200, resp.text
    assert fake_measure.calls[0]["group_by"] == "product" and fake_measure.calls[0]["top_n"] == 3


def test_ac5_a_dealer_outside_the_fake_measures_dealer_dimensions_is_403(client, db, fake_measure):
    w = _ranking_world(db)
    resp = _report(client, _dealer(db, w), measure="zzt_fake_measure", group_by="month", top_n=3)
    assert resp.status_code == 403 and _code(resp) == "report_dimension_not_allowed", resp.text
    assert fake_measure.calls == []


def test_review_a_measures_own_grant_is_enforced(client, db, register_fake, monkeypatch):
    """A contact holding the sales report grant but not the measure's grant is refused."""
    from app.services.reports import measures

    monkeypatch.setattr(measures, "KNOWN_GRANTS", measures.KNOWN_GRANTS | {"zzt.other"})
    m = register_fake("zzt_other_grant", grant="zzt.other")
    resp = _report(client, _full(db), measure=m.key)
    assert resp.status_code == 403 and _code(resp) == "sales_report_not_enabled", resp.text


def test_review_a_basis_the_ask_engine_does_not_know_is_served_with_the_measures_label(client, db, register_fake):
    m = register_fake("zzt_pending_m", basis="zzt_pending", label="Pending things")
    resp = _report(client, _full(db), measure=m.key)
    assert resp.status_code == 200, resp.text
    assert resp.json()["basis_label"] == "Pending things", resp.text


@pytest.mark.parametrize(
    "extra",
    [
        "other_customer",
        {"sales_agent_ids": ["00000000-0000-4000-8000-000000000001"]},
        {"customer_agent_ids": ["00000000-0000-4000-8000-000000000001"]},
        {"warehouse_codes": ["ZZT-WH"]},
        {"channel": "dealer"},
    ],
)
def test_review_a_dealer_may_not_widen_with_staff_filters_or_another_customer(client, db, extra):
    from tests._pg_fixture import unique_code
    from tests._sales_report_do_seed import seed_customer

    w = _ranking_world(db)
    if extra == "other_customer":
        extra = {"customer_ids": [seed_customer(db, name=unique_code("OtherCust")).id]}
        db.commit()
    resp = _report(client, _dealer(db, w), measure="delivered_amount", **extra)
    assert resp.status_code == 403, resp.text


# ------------------------------------------------------------------ AC-10 rate limit


def test_ac10_the_rate_limit_is_report_asks_setting_and_answers_busy(client, db, monkeypatch):
    from app.models.user import SystemSetting
    from app.services import rate_limit

    row = db.query(SystemSetting).first()
    if row is None:
        import uuid

        row = SystemSetting(id=str(uuid.uuid4()), name="ZZT Settings")
        db.add(row)
    row.report_ask_rate_limit, row.report_ask_rate_window_minutes = 2, 3
    db.commit()
    calls = []

    def _hit(bucket, ident, *, limit, window_seconds):
        calls.append((limit, window_seconds))
        return rate_limit.RateResult(allowed=False)

    monkeypatch.setattr(rate_limit, "hit", _hit)
    body = _report(client, _full(db), measure="delivered_amount").json()
    assert calls[-1] == (2, 180), calls
    assert body["status"] == "busy" and body["message"] == "Too many reports in the last 3 minutes.", body
