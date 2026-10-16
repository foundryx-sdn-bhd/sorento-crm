"""Dealer targets on the one target engine: model, achievement and API (lane SALES-TARGET-DEALER,
UAC D2). Plan: documentation/plans/sales/PLAN-dealer-sales-targets-15oct.md.

A dealer is a third subject on `sales.targets`: it counts a list of accounts (`customer_ids`,
any agent) and pays to `payout_customer_id`. Every subject can use thresholds in % / RM /
points, tiers per period, stacking "+ Additional" lines and rounding to sen or ringgit.

Seeds and fixtures are the S1 file's (`test_sales_targets_s1.py`); today is 20 Oct 2026.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .test_sales_targets_s1 import (  # noqa: F401  (fixtures)
    BASE,
    _agent,
    _category,
    _client,
    _code,
    _product,
    _restore,
    _so_line,
    _uid,
    api,
    world,
)

RATIOS = f"{BASE}/point-ratios"


def _customer(db, company_id, name=None, *, level=None, group_id=None):
    from app.models.order import Customer

    row = Customer(
        id=_uid(), company_id=company_id, customer_code=_code(), customer_name=name or f"ZZT {_code()}",
        account_level=level, customer_group_id=group_id,
    )
    db.add(row)
    db.flush()
    return row


def _group(db, company_id, name="ZZT Dealer A Sdn Bhd"):
    from app.models.order import CustomerGroup

    row = CustomerGroup(id=_uid(), company_id=company_id, name=name)
    db.add(row)
    db.flush()
    return row


def _product_id(db, company_id):
    return _product(db, company_id, _category(db, company_id).id).id


def _sale(db, company_id, customer, amount, on, *, product_id, agent=None, **kw):
    return _so_line(
        db, company_id, customer_id=customer.id, agent_id=agent.id if agent else None,
        order_date=on, line_total=Decimal(str(amount)), product_id=product_id, **kw,
    )


def _dealer_body(accounts, **extra):
    body = {
        "subject_kind": "dealer",
        "customer_ids": [a.id for a in accounts],
        "name": "ZZT 2026 quarterly",
        "metric": "amount",
        "basis": "ordered",
        "product_scope": "all",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "split_every": 3,
        "split_unit": "month",
        "commission_method": "retroactive" if extra.get("tiers") else "none",
        "threshold_unit": "amount",
        "tiers_per_period": True,
        "tiers": [],
    }
    body.update(extra)
    return body


def _period(body, start):
    return next(p for p in body["periods"] if p["period_start"] == start)


# --------------------------------------------------------------------------------------- #
# D2-1 create
# --------------------------------------------------------------------------------------- #


def test_create_dealer_target(api):
    client, db, company_id = api
    group = _group(db, company_id)
    a1 = _customer(db, company_id, "ZZT DEALER A", level=1, group_id=group.id)
    a3 = _customer(db, company_id, "ZZT DEALER A III", level=3, group_id=group.id)
    res = client.post(BASE, json=_dealer_body([a1, a3], payout_customer_id=a1.id, tiers=[
        {"period_start": "2026-04-01", "threshold": 200000, "account_rates": [{"customer_id": a1.id, "rate": 2}]},
        {"period_start": "2026-04-01", "threshold": 250000, "account_rates": [{"customer_id": a1.id, "rate": 3}]},
    ]))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["subject_kind"] == "dealer"
    # Accounts sharing a customer group: the dealer is the group's name.
    assert body["subject_label"] == "ZZT Dealer A Sdn Bhd"
    assert [a["label"] for a in body["accounts"]] == ["A/C I", "A/C III"]
    assert [a["code"] for a in body["accounts"]] == [a1.customer_code, a3.customer_code]
    assert body["payout_account"]["code"] == a1.customer_code
    assert body["threshold_unit"] == "amount"
    assert body["rounding"] == "sen"
    assert body["tiers_per_period"] is True
    assert len(body["periods"]) == 4
    q2 = [t for t in body["tiers"] if t["period_start"] == "2026-04-01"]
    assert [t["threshold"] for t in q2] == [200000, 250000]
    assert q2[1]["account_rates"] == [{"customer_id": a1.id, "rate": 3}]
    # A period's figure, when no target figure is typed: its lowest main threshold.
    assert _period(body, "2026-04-01")["target_value"] == 200000


def test_dealer_payout_defaults_to_first_account_and_label_falls_back_to_name(api):
    client, db, company_id = api
    c = _customer(db, company_id, "ZZT CERAMIC")
    d = _customer(db, company_id, "ZZT IBORN")
    res = client.post(BASE, json=_dealer_body([c, d]))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["payout_account"]["code"] == c.customer_code
    assert body["subject_label"] == "ZZT CERAMIC"
    assert [a["label"] for a in body["accounts"]] == ["ZZT CERAMIC", "ZZT IBORN"]


# --------------------------------------------------------------------------------------- #
# D2-4 achievement and D1 examples through the API
# --------------------------------------------------------------------------------------- #


def test_dealer_achievement_counts_listed_accounts_only_any_agent(api):
    client, db, company_id = api
    pid = _product_id(db, company_id)
    a1 = _customer(db, company_id, "ZZT A1", level=1)
    a3 = _customer(db, company_id, "ZZT A3", level=3)
    other = _customer(db, company_id, "ZZT OTHER")
    agent_x, agent_y = _agent(db, "X"), _agent(db, "Y")
    _sale(db, company_id, a1, 100000, date(2026, 5, 3), product_id=pid, agent=agent_x)
    _sale(db, company_id, a1, 80000, date(2026, 6, 30), product_id=pid, agent=agent_y)
    _sale(db, company_id, a3, 70000, date(2026, 4, 1), product_id=pid)
    _sale(db, company_id, other, 999999, date(2026, 5, 3), product_id=pid)               # not counted
    _sale(db, company_id, a3, 55555, date(2026, 5, 3), product_id=pid, status="cancelled")  # cancelled
    _sale(db, company_id, a1, 44444, date(2026, 7, 1), product_id=pid)                   # Q3
    res = client.post(BASE, json=_dealer_body([a1, a3], payout_customer_id=a1.id, tiers=[
        {"period_start": "2026-04-01", "threshold": 200000, "account_rates": [{"customer_id": a1.id, "rate": 2}]},
        {"period_start": "2026-04-01", "threshold": 250000, "account_rates": [{"customer_id": a1.id, "rate": 3}]},
    ]))
    assert res.status_code == 201, res.text
    body = client.get(f"{BASE}/{res.json()['id']}").json()
    q2 = _period(body, "2026-04-01")
    assert q2["achieved_value"] == 250000
    # E1: tier 2 reached on A/C I + III, paid on A/C I only: 3% x 180,000.
    assert q2["commission_earned"] == 5400
    assert [line["amount"] for line in q2["commission_lines"]] == [5400]
    by_account = {a["code"]: a["achieved_value"] for a in q2["accounts"]}
    assert by_account == {a1.customer_code: 180000, a3.customer_code: 70000}
    assert _period(body, "2026-07-01")["achieved_value"] == 44444


def test_pay_to_account_sales_do_not_count_unless_counted(api):
    client, db, company_id = api
    pid = _product_id(db, company_id)
    a1 = _customer(db, company_id, "ZZT A1", level=1)
    a3 = _customer(db, company_id, "ZZT A3", level=3)
    _sale(db, company_id, a3, 90000, date(2026, 5, 1), product_id=pid)
    _sale(db, company_id, a1, 50000, date(2026, 5, 1), product_id=pid)
    res = client.post(BASE, json=_dealer_body([a3], payout_customer_id=a1.id, tiers=[
        {"period_start": "2026-04-01", "threshold": 80000, "rate": 2},
    ]))
    assert res.status_code == 201, res.text
    body = res.json()
    q2 = _period(body, "2026-04-01")
    # E6: 2% x 90,000 on A/C III, paid to A/C I; A/C I's own 50,000 is not counted.
    assert q2["achieved_value"] == 90000
    assert q2["commission_earned"] == 1800
    assert body["payout_account"]["code"] == a1.customer_code


def test_additional_lines_stack_via_api_and_round_per_target(api):
    client, db, company_id = api
    pid = _product_id(db, company_id)
    a1 = _customer(db, company_id, "ZZT B1", level=1)
    _sale(db, company_id, a1, "253450.50", date(2026, 2, 10), product_id=pid)
    tiers = [
        {"period_start": "2026-01-01", "threshold": 150000, "rate": 2},
        {"period_start": "2026-01-01", "threshold": 200000, "rate": 3},
        {"period_start": "2026-01-01", "threshold": 220000, "rate": 1, "is_additional": True},
        {"period_start": "2026-01-01", "threshold": 250000, "rate": 0.5, "is_additional": True},
    ]
    sen = client.post(BASE, json=_dealer_body([a1], tiers=tiers)).json()
    q1 = _period(sen, "2026-01-01")
    assert [line["amount"] for line in q1["commission_lines"]] == [7603.52, 2534.51, 1267.25]
    assert [line["kind"] for line in q1["commission_lines"]] == ["main", "additional", "additional"]
    assert q1["commission_earned"] == 11405.28
    ringgit = client.post(BASE, json=_dealer_body([a1], tiers=tiers, rounding="ringgit")).json()
    assert _period(ringgit, "2026-01-01")["commission_earned"] == 11406
    # A period with no tiers of its own pays nothing.
    assert _period(sen, "2026-04-01")["commission_earned"] == 0


def test_rate_per_account_deluxe_mastile_wply(api):
    client, db, company_id = api
    pid = _product_id(db, company_id)
    deluxe = _customer(db, company_id, "ZZT DELUXE")
    mastile = _customer(db, company_id, "ZZT MASTILE")
    wply = _customer(db, company_id, "ZZT W-PLY")
    for acc, amount in ((deluxe, 150000), (mastile, 100000), (wply, 60000)):
        _sale(db, company_id, acc, amount, date(2026, 8, 1), product_id=pid)
    res = client.post(BASE, json=_dealer_body([deluxe, mastile, wply], tiers=[
        {"period_start": "2026-07-01", "threshold": 300000, "account_rates": [
            {"customer_id": deluxe.id, "rate": 2},
            {"customer_id": mastile.id, "rate": 3},
            {"customer_id": wply.id, "rate": 1.5},
        ]},
    ]))
    assert res.status_code == 201, res.text
    assert _period(res.json(), "2026-07-01")["commission_earned"] == 6900


def test_agent_target_fixed_tiers_per_period(api):
    client, db, company_id = api
    pid = _product_id(db, company_id)
    agent = _agent(db, "A")
    cust = _customer(db, company_id)
    _sale(db, company_id, cust, 612000, date(2026, 2, 1), product_id=pid, agent=agent)
    res = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT agent quarterly",
        "metric": "amount", "basis": "ordered", "product_scope": "all",
        "start_date": "2026-01-01", "end_date": "2026-12-31", "split_every": 3, "split_unit": "month",
        "commission_method": "retroactive", "threshold_unit": "amount", "tiers_per_period": True,
        "tiers": [
            {"period_start": "2026-01-01", "threshold": 500000, "rate": 1},
            {"period_start": "2026-01-01", "threshold": 600000, "rate": 1.5},
        ],
    })
    assert res.status_code == 201, res.text
    q1 = _period(res.json(), "2026-01-01")
    # E7: highest reached 1.5% x 612,000.
    assert q1["commission_earned"] == 9180
    assert q1["target_value"] == 500000


def test_points_target_uses_ratio_on_period_last_day(api):
    client, db, company_id = api
    pid = _product_id(db, company_id)
    c = _customer(db, company_id, "ZZT CERAMIC")
    _sale(db, company_id, c, 130000, date(2026, 8, 1), product_id=pid)
    _sale(db, company_id, c, 125000, date(2026, 11, 1), product_id=pid)
    for effective_from, ratio in (("2026-01-01", 1.20), ("2026-10-01", 1.30)):
        res = client.post(RATIOS, json={"effective_from": effective_from, "rm_per_point": ratio})
        assert res.status_code in (200, 201), res.text
    res = client.post(BASE, json=_dealer_body([c], threshold_unit="points", tiers=[
        {"period_start": "2026-07-01", "threshold": 100000, "rate": 4},
        {"period_start": "2026-10-01", "threshold": 100000, "rate": 4},
    ]))
    assert res.status_code == 201, res.text
    body = res.json()
    q3, q4 = _period(body, "2026-07-01"), _period(body, "2026-10-01")
    assert q3["point_ratio"] == 1.2 and q4["point_ratio"] == 1.3
    assert q3["commission_earned"] == 5200
    assert q4["commission_earned"] == 0
    # Q1 has no tiers; Q2's last day still has a ratio, so nothing is missing there.
    assert q3["ratio_missing"] is False


def test_points_ratio_is_read_on_the_period_last_day_not_its_first_or_today(api):
    """UAC D1-5 (review kill test M3a/M3b): the ratio changes mid-quarter (15 Aug) and again
    after the quarter (5 Oct, before today). Q3's 125,000 sits between the converted
    thresholds: 120,000 at the first-day ratio, 130,000 at the last-day ratio, 110,000 at
    today's. Only the last day's ratio leaves it unreached."""
    client, db, company_id = api
    pid = _product_id(db, company_id)
    c = _customer(db, company_id, "ZZT CERAMIC")
    _sale(db, company_id, c, 125000, date(2026, 8, 1), product_id=pid)
    for effective_from, ratio in (("2026-01-01", 1.20), ("2026-08-15", 1.30), ("2026-10-05", 1.10)):
        res = client.post(RATIOS, json={"effective_from": effective_from, "rm_per_point": ratio})
        assert res.status_code in (200, 201), res.text
    res = client.post(BASE, json=_dealer_body([c], threshold_unit="points", tiers=[
        {"period_start": "2026-07-01", "threshold": 100000, "rate": 4},
    ]))
    assert res.status_code == 201, res.text
    q3 = _period(res.json(), "2026-07-01")
    assert q3["point_ratio"] == 1.3
    assert q3["target_value"] == 130000
    assert q3["commission_earned"] == 0


def test_points_target_with_no_tiers_this_period_still_flags_a_missing_ratio(api):
    client, db, company_id = api
    c = _customer(db, company_id, "ZZT CERAMIC")
    res = client.post(BASE, json=_dealer_body([c], threshold_unit="points", tiers=[
        {"period_start": "2026-07-01", "threshold": 1, "rate": 4},
    ]))
    assert res.status_code == 201, res.text
    # Q1 has no tiers of its own and no ratio is on file at all.
    assert _period(res.json(), "2026-01-01")["ratio_missing"] is True


def test_points_target_without_ratio_flags_it(api):
    client, db, company_id = api
    c = _customer(db, company_id, "ZZT CERAMIC")
    res = client.post(BASE, json=_dealer_body([c], threshold_unit="points", tiers=[
        {"period_start": "2026-01-01", "threshold": 1, "rate": 4},
    ]))
    assert res.status_code == 201, res.text
    q1 = _period(res.json(), "2026-01-01")
    assert q1["ratio_missing"] is True
    assert q1["commission_earned"] == 0


# --------------------------------------------------------------------------------------- #
# D2-7 point ratios
# --------------------------------------------------------------------------------------- #


def test_point_ratios_dated_history_one_row_per_date(api):
    client, db, company_id = api
    assert client.post(RATIOS, json={"effective_from": "2026-01-01", "rm_per_point": 1.2}).status_code in (200, 201)
    assert client.post(RATIOS, json={"effective_from": "2026-10-01", "rm_per_point": 1.3}).status_code in (200, 201)
    # The same date again replaces that date's ratio, never a second row.
    assert client.post(RATIOS, json={"effective_from": "2026-10-01", "rm_per_point": 1.35}).status_code in (200, 201)
    rows = client.get(RATIOS).json()["rows"]
    assert [(r["effective_from"], r["rm_per_point"]) for r in rows][:2] == [
        ("2026-10-01", 1.35), ("2026-01-01", 1.2),
    ]
    assert client.post(RATIOS, json={"effective_from": "2026-11-01", "rm_per_point": 0}).status_code == 422


# --------------------------------------------------------------------------------------- #
# D2-3 validation, D2-10 company scope
# --------------------------------------------------------------------------------------- #


def test_tier_validation_refuses_in_plain_words(api):
    client, db, company_id = api
    a1 = _customer(db, company_id, "ZZT A1")
    stranger = _customer(db, company_id, "ZZT NOT COUNTED")

    def refused(**extra):
        res = client.post(BASE, json=_dealer_body([a1], **extra))
        assert res.status_code == 422, res.text
        return res.json()

    refused(tiers=[{"period_start": "2026-01-01", "threshold": 1, "account_rates": [
        {"customer_id": stranger.id, "rate": 2}]}])
    refused(tiers=[{"period_start": "2026-02-01", "threshold": 1, "rate": 2}])  # not a period start
    refused(tiers=[
        {"period_start": "2026-01-01", "threshold": 5, "rate": 2},
        {"period_start": "2026-01-01", "threshold": 5, "rate": 3},
    ])
    refused(tiers=[{"threshold": 1, "rate": 2}])  # per period without a period
    refused(threshold_unit="points", metric="quantity", tiers=[
        {"period_start": "2026-01-01", "threshold": 1, "rate": 2}])
    refused(commission_method="marginal", tiers=[{"period_start": "2026-01-01", "threshold": 1,
                                                  "account_rates": [{"customer_id": a1.id, "rate": 2}]}])
    refused(tiers=[{"period_start": "2026-01-01", "rate": 2}])  # no threshold
    res = client.post(BASE, json=_dealer_body([]))
    assert res.status_code == 422


def test_dealer_accounts_company_scoped(api):
    client, db, company_id = api
    from sqlalchemy import text

    other_company = db.execute(
        text("select id from companies where id <> :c order by code limit 1"), {"c": company_id}
    ).scalar()
    mine = _customer(db, company_id, "ZZT MINE")
    from app.models.base import company_scope

    with company_scope(db, frozenset({other_company})):
        theirs = _customer(db, other_company, "ZZT THEIRS")
    assert client.post(BASE, json=_dealer_body([mine, theirs])).status_code == 422
    assert client.post(BASE, json=_dealer_body([mine], payout_customer_id=theirs.id)).status_code == 422


# --------------------------------------------------------------------------------------- #
# D2-8 existing targets unchanged
# --------------------------------------------------------------------------------------- #


def test_existing_agent_target_reads_as_before(api):
    client, db, company_id = api
    pid = _product_id(db, company_id)
    agent = _agent(db, "A")
    _so_line(db, company_id, agent_id=agent.id, order_date=date(2026, 10, 5),
             line_total=Decimal("1500"), product_id=pid)
    res = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT old style",
        "metric": "amount", "basis": "ordered", "product_scope": "all",
        "start_date": "2026-10-01", "end_date": "2026-10-31", "target_value": 1000,
        "commission_method": "marginal",
        "tiers": [{"from_pct": 0, "rate": 2, "bonus_amount": 50}, {"from_pct": 100, "rate": 4, "bonus_amount": 100}],
    })
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["threshold_unit"] == "pct"
    assert body["rounding"] == "sen"
    assert body["tiers_per_period"] is False
    period = body["periods"][0]
    # Marginal: 2% x 1,000 + 4% x 500 = 40; bonuses 50 + 100.
    assert period["commission_earned"] == 40
    assert period["bonus_earned"] == 150
    assert [t["from_pct"] for t in body["tiers"]] == [0, 100]


# --------------------------------------------------------------------------------------- #
# D2-6 list
# --------------------------------------------------------------------------------------- #


def test_list_dealer_targets(api):
    client, db, company_id = api
    pid = _product_id(db, company_id)
    a1 = _customer(db, company_id, "ZZT A1", level=1)
    a3 = _customer(db, company_id, "ZZT A3", level=3)
    _sale(db, company_id, a1, 180000, date(2026, 10, 3), product_id=pid)
    _sale(db, company_id, a3, 70000, date(2026, 10, 3), product_id=pid)
    created = client.post(BASE, json=_dealer_body([a1, a3], payout_customer_id=a1.id, tiers=[
        {"period_start": "2026-10-01", "threshold": 250000, "account_rates": [{"customer_id": a1.id, "rate": 3}]},
    ])).json()
    rows = client.get(BASE, params={"subject": "dealer", "all": "true"}).json()["rows"]
    row = next(r for r in rows if r["target_id"] == created["id"])
    assert row["subject_kind"] == "dealer"
    assert row["account_labels"] == ["A/C I", "A/C III"]
    assert row["payout_label"] == "A/C I"
    # Today is 20 Oct 2026: the current period is Q4.
    assert row["commission_earned"] == 5400
    # Agent and team tabs do not list dealer targets.
    agent_rows = client.get(BASE, params={"subject": "agent", "all": "true"}).json()["rows"]
    assert all(r["target_id"] != created["id"] for r in agent_rows)
    searched = client.get(BASE, params={"subject": "dealer", "all": "true", "query": a3.customer_code}).json()
    assert [r["target_id"] for r in searched["rows"]] == [created["id"]]


def test_edit_dealer_accounts_and_tiers(api):
    client, db, company_id = api
    a1 = _customer(db, company_id, "ZZT A1", level=1)
    a3 = _customer(db, company_id, "ZZT A3", level=3)
    created = client.post(BASE, json=_dealer_body([a1])).json()
    res = client.patch(f"{BASE}/{created['id']}", json={
        "customer_ids": [a1.id, a3.id], "payout_customer_id": a3.id, "rounding": "ringgit",
        "commission_method": "retroactive",
        "tiers": [{"period_start": "2026-01-01", "threshold": 10, "rate": 1}],
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert [a["code"] for a in body["accounts"]] == [a1.customer_code, a3.customer_code]
    assert body["payout_account"]["code"] == a3.customer_code
    assert body["rounding"] == "ringgit"
    assert [t["threshold"] for t in body["tiers"]] == [10]


# --------------------------------------------------------------------------------------- #
# D2-9 RBAC
# --------------------------------------------------------------------------------------- #


def test_dealer_routes_403(world):
    db, company_id = world
    client, originals = _client(db, ["sales.targets.view"])
    try:
        assert client.get(RATIOS).status_code == 200
        assert client.post(RATIOS, json={"effective_from": "2026-01-01", "rm_per_point": 1}).status_code == 403
        files = {"file": ("x.xlsx", b"not a workbook", "application/octet-stream")}
        assert client.post(f"{BASE}/import", params={"dry_run": "true"}, files=files).status_code == 403
    finally:
        _restore(originals)


def test_per_period_tiers_save_without_autoflush(api):
    """`SessionLocal` runs with autoflush off: periods added in the same request must be
    flushed before the tiers are checked against them (found by the import's local dry run)."""
    client, db, company_id = api
    a1 = _customer(db, company_id, "ZZT A1")
    db.autoflush = False
    try:
        res = client.post(BASE, json=_dealer_body([a1], tiers=[
            {"period_start": "2026-04-01", "threshold": 1000, "rate": 2},
        ]))
        assert res.status_code == 201, res.text
        agent = _agent(db, "A")
        res = client.post(BASE, json={
            "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT agent q",
            "metric": "amount", "basis": "ordered", "product_scope": "all",
            "start_date": "2026-01-01", "end_date": "2026-12-31", "split_every": 3, "split_unit": "month",
            "commission_method": "retroactive", "threshold_unit": "amount", "tiers_per_period": True,
            "tiers": [{"period_start": "2026-07-01", "threshold": 10, "rate": 1}],
        })
        assert res.status_code == 201, res.text
        assert res.json()["periods"][2]["target_value"] == 10
    finally:
        db.autoflush = True


def test_removing_an_account_drops_its_tier_rates(api):
    client, db, company_id = api
    a1 = _customer(db, company_id, "ZZT A1", level=1)
    a3 = _customer(db, company_id, "ZZT A3", level=3)
    created = client.post(BASE, json=_dealer_body([a1, a3], tiers=[
        {"period_start": "2026-01-01", "threshold": 10, "account_rates": [
            {"customer_id": a1.id, "rate": 2}, {"customer_id": a3.id, "rate": 3}]},
    ])).json()
    res = client.patch(f"{BASE}/{created['id']}", json={"customer_ids": [a1.id]})
    assert res.status_code == 200, res.text
    assert res.json()["tiers"][0]["account_rates"] == [{"customer_id": a1.id, "rate": 2}]
    from app.models.sales import SalesTargetTierRate

    assert db.query(SalesTargetTierRate).filter(SalesTargetTierRate.customer_id == a3.id).count() == 0


# --------------------------------------------------------------------------------------- #
# Crew ruling 16 Oct (from owner ruling 4, "highest tier reached pays"): Tier 1-5 are one scale;
# an account's own Tier 1-3 target is REPLACED by its dealer's Tier 4-5 target once the
# dealer's combined sales reach it in the same year.
# --------------------------------------------------------------------------------------- #


def _yearly(accounts, tiers, **extra):
    return _dealer_body(
        accounts, name="ZZT yearly", split_every=None, split_unit=None, tiers_per_period=False,
        tiers=tiers, **extra,
    )


def _replacing_pair(client, db, company_id, a3_sales):
    pid = _product_id(db, company_id)
    a1 = _customer(db, company_id, "ZZT REPL A/C I", level=1)
    a3 = _customer(db, company_id, "ZZT REPL A/C III", level=3)
    _sale(db, company_id, a1, 800000, date(2026, 3, 1), product_id=pid)
    _sale(db, company_id, a3, a3_sales, date(2026, 3, 1), product_id=pid)
    dealer = client.post(BASE, json=_yearly([a1, a3], [
        {"threshold": 2000000, "rate": 0, "account_rates": [
            {"customer_id": a1.id, "rate": 2}, {"customer_id": a3.id, "rate": 2.5},
        ]},
    ]))
    assert dealer.status_code == 201, dealer.text
    own = client.post(BASE, json=_yearly([a1], [
        {"threshold": 300000, "rate": 0.5}, {"threshold": 700000, "rate": 1},
    ], replaced_by_target_id=dealer.json()["id"]))
    assert own.status_code == 201, own.text
    return dealer.json(), own.json()


def test_a_reached_dealer_tier_replaces_the_account_s_own_tier(api):
    """A/C I sells 800k (its own Tier 2 at 1% = 8,000); the dealer sells 2.1M combined, past
    its 2M tier: A/C I gets the dealer's 2% = 16,000, never 24,000."""
    client, db, company_id = api
    dealer, own = _replacing_pair(client, db, company_id, 1300000)
    assert dealer["periods"][0]["commission_earned"] == 16000 + 32500
    assert own["periods"][0]["commission_earned"] == 0
    assert own["periods"][0]["replaced"] is True
    assert own["replaced_by"]["target_no"] == dealer["target_no"]


def test_below_the_dealer_tier_the_account_pays_its_own_tier(api):
    client, db, company_id = api
    dealer, own = _replacing_pair(client, db, company_id, 1000000)   # 1.8M combined
    assert dealer["periods"][0]["commission_earned"] == 0
    assert own["periods"][0]["commission_earned"] == 8000
    assert own["periods"][0]["replaced"] is False


def test_replaced_by_must_name_another_dealer_target(api):
    client, db, company_id = api
    a1 = _customer(db, company_id, "ZZT REPL X", level=1)
    team_or_none = client.post(BASE, json=_yearly([a1], [{"threshold": 1, "rate": 1}],
                                                   replaced_by_target_id=_uid()))
    assert team_or_none.status_code in (404, 422), team_or_none.text
    agent = _agent(db, company_id)
    res = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
        "start_date": "2026-01-01", "end_date": "2026-12-31", "target_value": 1,
        "replaced_by_target_id": _uid(),
    })
    assert res.status_code == 422, res.text


def test_replaced_by_refuses_an_agent_target_itself_and_a_chain(api):
    client, db, company_id = api
    agent = _agent(db, company_id)
    agent_target = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT A", "metric": "amount",
        "start_date": "2026-01-01", "end_date": "2026-12-31", "target_value": 1,
    })
    assert agent_target.status_code == 201, agent_target.text
    a1 = _customer(db, company_id, "ZZT CHAIN 1", level=1)
    tiers = [{"threshold": 1, "rate": 1}]
    res = client.post(BASE, json=_yearly([a1], tiers, replaced_by_target_id=agent_target.json()["id"]))
    assert res.status_code == 422, res.text
    top = client.post(BASE, json=_yearly([a1], tiers)).json()
    mid = client.post(BASE, json=_yearly([a1], tiers, replaced_by_target_id=top["id"])).json()
    assert client.patch(f"{BASE}/{top['id']}", json={"replaced_by_target_id": top["id"]}).status_code == 422
    chained = client.post(BASE, json=_yearly([a1], tiers, replaced_by_target_id=mid["id"]))
    assert chained.status_code == 422, chained.text
