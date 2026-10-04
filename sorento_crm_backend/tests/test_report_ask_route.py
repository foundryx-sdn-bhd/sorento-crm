"""Phase 2 RED tests - `GET /api/v1/order-management/report-ask` (lane REPORT-ENGINE, slice 1a).

`documentation/plans/chatbot/PLAN-report-engine.md` section 0 and section 10 (the binding
contract) and `report-engine-acceptance-criteria.md` AC-RE-1 to AC-RE-17 and AC-RE-20.

Postgres only (`tests/_pg_fixture.py`), every row seeded here (CI's database has none).

Owner direction 4 Oct 2026: EVERY basis (delivered, ordered, outstanding) ranks from SALES ORDER
lines with the SO's own sales agent; delivery orders and the DO-to-SO link are not used. A
"sale" in this file is one SO with one fully delivered line (`tests/_report_ask_so_seed.py`), so
a line's delivered amount is its `line_total`. The per-line figures and the line's date
(`coalesce(required_date, order_date)`) are top selling's own (`sales_report_service`).

Ambiguities the tester did NOT resolve by guessing (flagged to the captain):
* a dealer naming another customer: PLAN section 10 lists the customer filter among the
  dimensions that 403 `report_dimension_not_allowed`, AC-RE-14 says 403 `customer_not_permitted`.
  Pinned here as AC-RE-14 (`customer_not_permitted`).
* the row `name` for `group_by=channel` (raw `project` / `retail` or a label): not asserted,
  only the amounts and the order.
* `group_by=month` rows rank by the measure like every other group (not chronologically).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.order import SalesOrder, SalesOrderLine
from app.models.product import Brand, ProductCategory
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._mc_lookup_seed import seed_mocha
from tests._report_ask_so_seed import seed_so_line
from tests._sales_report_do_seed import (  # noqa: F401  (client / db are fixtures)
    _as_contact,
    _contact,
    _link,
    client,
    db,
    money,
    product,
    seed_agent,
    seed_customer,
    seed_policy,
    warehouse,
)
from tests.test_top_selling_report import _access
from tests._pg_fixture import unique_code

D = Decimal
ROUTE = "/api/v1/order-management/report-ask"
SALES_REPORT = "/api/v1/order-management/sales-report"
KEY = {"X-API-Key": "k"}
PERIOD = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
SEP = date(2026, 9, 10)
AUG = date(2026, 8, 15)
DEALER_MESSAGE = "That breakdown is not available for your account."


# ------------------------------------------------------------------------- helpers


def _ask(client, contact, headers=KEY, **params):
    q = {**PERIOD, **_as_contact(contact), **params}
    q = {k: v for k, v in q.items() if v is not None}
    return client.get(ROUTE, params=q, headers=headers)


def _ok(client, contact, **params):
    resp = _ask(client, contact, **params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _code(resp):
    return resp.json().get("code")


def _rows(body):
    return [(r["name"], r["qty"], money(r["amount"])) for r in body["rows"]]


def _full(db):
    """A grant holder with no customer link: the FULL audience."""
    contact = _contact(db)
    db.commit()
    return contact


def _brand(db, name):
    row = Brand(id=str(uuid.uuid4()), brand_code=unique_code("BR")[:50], brand_name=name,
                company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def _category(db, name):
    row = ProductCategory(id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50],
                          category_name=name, company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def _prod(db, code, *, brand=None, category=None):
    row = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    if brand is not None:
        row.brand_id = brand.id
    if category is not None:
        row.category_id = category.id
    db.flush()
    return row


def _sale(db, *, cust, wh, agent, prod, qty, total, when=SEP):
    """One SO of `agent` (None = an SO with no agent) with one fully delivered line."""
    return seed_so_line(db, cust=cust, prod=prod, qty=qty, total=total, agent=agent, wh=wh, order_date=when)


def _ranking_world(db):
    """Three agents, two brands, two categories, three products (see the table below).

    p1 ZZTAA-1 brand A cat X | p2 ZZTAA-2 brand A cat Y | p3 ZZTBB-1 brand B cat X

        agent  p1          p2         p3          all
        A1     1000 / 10   -          500 / 5     1500 / 15
        A2     600 / 20    100 / 4    -           700 / 24
        A3     -           50 / 1     3000 / 30   3050 / 31
        A4 (August, outside the window)   777 / 7

    all (Sep): A3 3050/31, A1 1500/15, A2 700/24 -> total 5250 / 70.
    """
    w = SimpleNamespace()
    w.cust = seed_customer(db, name=unique_code("Cust"))
    w.wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    w.brand_a, w.brand_b = _brand(db, "ZZT-BRAND-A"), _brand(db, "ZZT-BRAND-B")
    w.cat_x, w.cat_y = _category(db, "ZZT-CAT-X"), _category(db, "ZZT-CAT-Y")
    w.p1 = _prod(db, "ZZTAA-1", brand=w.brand_a, category=w.cat_x)
    w.p2 = _prod(db, "ZZTAA-2", brand=w.brand_a, category=w.cat_y)
    w.p3 = _prod(db, "ZZTBB-1", brand=w.brand_b, category=w.cat_x)
    w.a1, w.a2, w.a3, w.a4 = (seed_agent(db, code=f"ZZT-AG-{n}") for n in ("1", "2", "3", "4"))
    kw = dict(cust=w.cust, wh=w.wh)
    _sale(db, agent=w.a1, prod=w.p1, qty=10, total="1000.00", **kw)
    _sale(db, agent=w.a1, prod=w.p3, qty=5, total="500.00", **kw)
    _sale(db, agent=w.a2, prod=w.p1, qty=20, total="600.00", **kw)
    _sale(db, agent=w.a2, prod=w.p2, qty=4, total="100.00", **kw)
    _sale(db, agent=w.a3, prod=w.p2, qty=1, total="50.00", **kw)
    _sale(db, agent=w.a3, prod=w.p3, qty=30, total="3000.00", **kw)
    _sale(db, agent=w.a4, prod=w.p1, qty=7, total="777.00", when=AUG, **kw)
    db.commit()
    return w


# ================================================================ AC-RE-1 / 2 / 3: the filters


def test_ac_re_1_rank_sales_agents_for_a_product_prefix(client, db):
    """AC-RE-1: ZZTAA matches p1 and p2 only: A1 1000/10, A2 700/24, A3 50/1; A4 (August) absent."""
    w = _ranking_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, product_code="zztaa")
    assert body["status"] == "ok" and body["basis"] == "delivered", body
    assert body["group_by"] == "sales_agent" and body["group_label"] == "Sales agent", body
    assert body["date_from"] == "2026-09-01" and body["date_to"] == "2026-09-30", body
    assert [r["rank"] for r in body["rows"]] == [1, 2, 3], body
    assert _rows(body) == [("ZZT-AG-1", 10, D("1000.00")), ("ZZT-AG-2", 24, D("700.00")),
                           ("ZZT-AG-3", 1, D("50.00"))], body["rows"]
    assert body["more"] == 0 and body["total_count"] == 3, body
    assert body["total"]["qty"] == 35 and money(body["total"]["amount"]) == D("1750.00"), body["total"]
    assert w.a4.sales_agent not in [r["name"] for r in body["rows"]]


def test_ac_re_2_rank_sales_agents_for_a_brand(client, db):
    """AC-RE-2: brand B is p3 only: A3 3000/30, A1 500/5."""
    w = _ranking_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, brand_ids=[w.brand_b.id])
    assert _rows(body) == [("ZZT-AG-3", 30, D("3000.00")), ("ZZT-AG-1", 5, D("500.00"))], body["rows"]
    assert body["total_count"] == 2 and money(body["total"]["amount"]) == D("3500.00"), body


def test_ac_re_3_rank_sales_agents_for_a_category(client, db):
    """AC-RE-3: category X is p1 and p3: A3 3000/30, A1 1500/15, A2 600/20."""
    w = _ranking_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, category_ids=[w.cat_x.id])
    assert _rows(body) == [("ZZT-AG-3", 30, D("3000.00")), ("ZZT-AG-1", 15, D("1500.00")),
                           ("ZZT-AG-2", 20, D("600.00"))], body["rows"]
    assert money(body["total"]["amount"]) == D("5100.00") and body["total"]["qty"] == 65, body["total"]


# ================================================================ AC-RE-4 to 7: ranking rules


def test_ac_re_4_top_n_cuts_rows_but_the_total_is_the_whole_set(client, db):
    """AC-RE-4: top_n=1 of 3 agents: 1 row, more=2, total_count=3, total = all three."""
    _ranking_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=1)
    assert _rows(body) == [("ZZT-AG-3", 31, D("3050.00"))], body["rows"]
    assert body["more"] == 2 and body["total_count"] == 3, body
    assert money(body["total"]["amount"]) == D("5250.00") and body["total"]["qty"] == 70, body["total"]


def test_ac_re_5_sort_asc_ranks_lowest_first_and_skips_agents_without_a_sale(client, db):
    """AC-RE-5: asc = A2 700, A1 1500, A3 3050; A4 (only an August SO) is not listed."""
    _ranking_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, sort="asc")
    assert [r["name"] for r in body["rows"]] == ["ZZT-AG-2", "ZZT-AG-1", "ZZT-AG-3"], body["rows"]
    assert [r["rank"] for r in body["rows"]] == [1, 2, 3], body["rows"]
    assert body["total_count"] == 3, body


def test_ac_re_6_measure_qty_ranks_by_quantity(client, db):
    """AC-RE-6: product ZZTAA: by amount A1, A2, A3; by qty A2 (24), A1 (10), A3 (1)."""
    _ranking_world(db)
    contact = _full(db)
    by_amount = _ok(client, contact, group_by="sales_agent", top_n=10, product_code="ZZTAA")
    assert [r["name"] for r in by_amount["rows"]] == ["ZZT-AG-1", "ZZT-AG-2", "ZZT-AG-3"], by_amount
    assert by_amount["measure"] == "amount", by_amount
    by_qty = _ok(client, contact, group_by="sales_agent", top_n=10, product_code="ZZTAA", measure="qty")
    assert by_qty["measure"] == "qty", by_qty
    assert [(r["name"], r["qty"]) for r in by_qty["rows"]] == [
        ("ZZT-AG-2", 24), ("ZZT-AG-1", 10), ("ZZT-AG-3", 1)], by_qty["rows"]


def test_ac_re_7_no_agent_is_one_row_and_the_rows_sum_to_the_total(client, db):
    """AC-RE-7: two SOs with no sales agent are ONE "(no agent)" row."""
    cust = seed_customer(db, name=unique_code("Cust"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    prod = _prod(db, "ZZTNOAG-1")
    agent = seed_agent(db, code="ZZT-AG-N")
    _sale(db, cust=cust, wh=wh, agent=agent, prod=prod, qty=1, total="10.00")
    _sale(db, cust=cust, wh=wh, agent=None, prod=prod, qty=2, total="20.00")
    _sale(db, cust=cust, wh=wh, agent=None, prod=prod, qty=3, total="30.00")
    db.commit()
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10)
    assert _rows(body) == [("(no agent)", 5, D("50.00")), ("ZZT-AG-N", 1, D("10.00"))], body["rows"]
    assert sum(money(r["amount"]) for r in body["rows"]) == money(body["total"]["amount"]), body
    assert money(body["total"]["amount"]) == D("60.00"), body


# ================================================================ AC-RE-8: basis and echo


def test_ac_re_8_ordered_basis_ranks_so_lines_filed_by_the_lines_required_date(client, db):
    """AC-RE-8 (owner direction 4 Oct): ordered = `qty_ordered` / `line_total`, a line dated by
    `coalesce(required_date, order_date)`. A2's Aug SO with a Sep required date is IN (a line
    filed by its SO date alone would drop it), a Sep SO whose line is required in Oct is OUT, a
    cancelled SO and a cancelled line are out. A2 5+50 / 300+9999, A1 10 / 1000, A3 2 / 200."""
    cust = seed_customer(db, name=unique_code("Cust"))
    prod = _prod(db, "ZZTORD-1")
    a1, a2, a3, a4 = (seed_agent(db, code=f"ZZT-OA-{n}") for n in ("1", "2", "3", "4"))
    kw = dict(cust=cust, prod=prod, delivered=0)
    seed_so_line(db, agent=a1, qty=10, total="1000.00", order_date=SEP, **kw)
    seed_so_line(db, agent=a2, qty=5, total="300.00", order_date=SEP, **kw)
    seed_so_line(db, agent=a2, qty=50, total="9999.00", order_date=AUG, required_date=date(2026, 9, 5), **kw)
    seed_so_line(db, agent=a4, qty=70, total="7000.00", order_date=SEP, required_date=date(2026, 10, 5), **kw)
    seed_so_line(db, agent=a3, qty=7, total="700.00", order_date=SEP, so_status="cancelled", **kw)
    seed_so_line(db, agent=a3, qty=8, total="800.00", order_date=SEP, line_status="cancelled", **kw)
    seed_so_line(db, agent=a3, qty=2, total="200.00", order_date=SEP, **kw)
    db.commit()
    contact = _full(db)
    body = _ok(client, contact, group_by="sales_agent", top_n=10, basis="ordered")
    assert body["basis"] == "ordered" and body["basis_label"] == "ordered sales", body
    assert _rows(body) == [("ZZT-OA-2", 55, D("10299.00")), ("ZZT-OA-1", 10, D("1000.00")),
                           ("ZZT-OA-3", 2, D("200.00"))], body["rows"]
    assert money(body["total"]["amount"]) == D("11499.00") and body["total"]["qty"] == 67, body["total"]
    # Nothing was delivered on any of these lines: the delivered basis finds nothing on the same data.
    delivered = _ok(client, contact, group_by="sales_agent", top_n=10, basis="delivered")
    assert delivered["basis"] == "delivered" and delivered["basis_label"] == "delivered sales", delivered
    assert delivered["rows"] == [] and delivered["total_count"] == 0, delivered


def test_ac_re_8_the_body_echoes_the_filters_by_name(client, db):
    """AC-RE-8: filters echo as {key, label, values:[names]}; the period is echoed."""
    w = _ranking_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=3, brand_ids=[w.brand_b.id])
    assert {"key": "brand", "label": "Brand", "values": ["ZZT-BRAND-B"]} in body["filters"], body["filters"]
    assert body["date_from"] == "2026-09-01" and body["date_to"] == "2026-09-30", body


# ================================================================ AC-RE-9: every dimension


def test_ac_re_9_group_by_customer_with_a_brand_filter(client, db):
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    brand_a, brand_b = _brand(db, "ZZT-BR-A"), _brand(db, "ZZT-BR-B")
    pa, pb = _prod(db, "ZZTCU-A", brand=brand_a), _prod(db, "ZZTCU-B", brand=brand_b)
    x, y = seed_customer(db, name="ZZT CUST X"), seed_customer(db, name="ZZT CUST Y")
    _sale(db, cust=x, wh=wh, agent=None, prod=pa, qty=1, total="100.00")
    _sale(db, cust=y, wh=wh, agent=None, prod=pa, qty=3, total="300.00")
    _sale(db, cust=y, wh=wh, agent=None, prod=pb, qty=9, total="999.00")  # other brand
    db.commit()
    body = _ok(client, _full(db), group_by="customer", top_n=5, brand_ids=[brand_a.id])
    assert body["group_by"] == "customer", body
    assert _rows(body) == [("ZZT CUST Y", 3, D("300.00")), ("ZZT CUST X", 1, D("100.00"))], body["rows"]


def test_ac_re_9_group_by_location_with_a_product_code(client, db):
    cust = seed_customer(db, name=unique_code("Cust"))
    wh_a = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-LOC-A")
    wh_b = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-LOC-B")
    prod = _prod(db, "ZZTLOC-1")
    _sale(db, cust=cust, wh=wh_a, agent=None, prod=prod, qty=10, total="100.00")
    _sale(db, cust=cust, wh=wh_b, agent=None, prod=prod, qty=30, total="300.00")
    db.commit()
    body = _ok(client, _full(db), group_by="location", top_n=5, product_code="ZZTLOC")
    assert _rows(body) == [("ZZT-LOC-B", 30, D("300.00")), ("ZZT-LOC-A", 10, D("100.00"))], body["rows"]


def test_ac_re_9_group_by_month_with_a_sales_agent_filter(client, db):
    cust = seed_customer(db, name=unique_code("Cust"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    prod = _prod(db, "ZZTMON-1")
    john, other = seed_agent(db, code="ZZT-JOHN"), seed_agent(db, code="ZZT-OTHER")
    _sale(db, cust=cust, wh=wh, agent=john, prod=prod, qty=2, total="200.00", when=date(2026, 8, 3))
    _sale(db, cust=cust, wh=wh, agent=john, prod=prod, qty=4, total="400.00", when=SEP)
    _sale(db, cust=cust, wh=wh, agent=other, prod=prod, qty=99, total="9900.00", when=SEP)
    db.commit()
    body = _ok(client, _full(db), group_by="month", top_n=12, sales_agent_ids=[john.id],
               date_from="2026-08-01", date_to="2026-09-30")
    assert _rows(body) == [("2026-09", 4, D("400.00")), ("2026-08", 2, D("200.00"))], body["rows"]
    assert money(body["total"]["amount"]) == D("600.00"), body


def test_ac_re_9_group_by_brand(client, db):
    _ranking_world(db)
    body = _ok(client, _full(db), group_by="brand", top_n=5)
    assert _rows(body) == [("ZZT-BRAND-B", 35, D("3500.00")), ("ZZT-BRAND-A", 35, D("1750.00"))], body["rows"]
    assert body["group_label"] == "Brand", body


def test_ac_re_9_group_by_category(client, db):
    _ranking_world(db)
    body = _ok(client, _full(db), group_by="category", top_n=5)
    assert _rows(body) == [("ZZT-CAT-X", 65, D("5100.00")), ("ZZT-CAT-Y", 5, D("150.00"))], body["rows"]
    assert body["group_label"] == "Category", body


def test_ac_re_9_group_by_product(client, db):
    _ranking_world(db)
    body = _ok(client, _full(db), group_by="product", top_n=5)
    assert [r["name"] for r in body["rows"]] == ["ZZTBB-1", "ZZTAA-1", "ZZTAA-2"], body["rows"]


def test_ac_re_9_group_by_channel(client, db):
    """The channel is the SO's own demand class (`retail` reads Dealer, `project` Project team)."""
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    prod = _prod(db, "ZZTCH-1")
    cust = seed_customer(db, name="ZZT CH CUSTOMER")
    seed_so_line(db, cust=cust, prod=prod, wh=wh, qty=1, total="100.00", demand_class="retail")
    seed_so_line(db, cust=cust, prod=prod, wh=wh, qty=3, total="300.00", demand_class="project")
    db.commit()
    body = _ok(client, _full(db), group_by="channel", top_n=5)
    assert body["group_by"] == "channel", body
    assert [(r["qty"], money(r["amount"])) for r in body["rows"]] == [(3, D("300.00")), (1, D("100.00"))], body
    # Captain ruling (PLAN section 10): channel rows read Dealer / Project team on every basis.
    assert [r["name"] for r in body["rows"]] == ["Project team", "Dealer"], body


# ================================================================ AC-RE-10 / 11: period, top_n


@pytest.mark.parametrize("missing", [("date_from",), ("date_to",), ("date_from", "date_to")])
def test_ac_re_10_period_is_required(client, db, missing):
    contact = _full(db)
    q = {**PERIOD, **_as_contact(contact), "group_by": "product", "top_n": 3}
    for key in missing:
        q.pop(key)
    resp = client.get(ROUTE, params=q, headers=KEY)
    assert resp.status_code == 422 and _code(resp) == "period_required", resp.text


def test_ac_re_11_group_by_without_top_n_is_422(client, db):
    resp = _ask(client, _full(db), group_by="sales_agent")
    assert resp.status_code == 422 and _code(resp) == "top_n_required", resp.text


def test_ac_re_11_no_group_by_and_no_top_n_is_the_number_shape(client, db):
    _ranking_world(db)
    body = _ok(client, _full(db))
    assert body["group_by"] is None and body["rows"] == [], body
    assert body["total"]["qty"] == 70 and money(body["total"]["amount"]) == D("5250.00"), body["total"]


# ================================================================ 422 codes


@pytest.mark.parametrize(
    "params, code",
    [
        ({"group_by": "colour", "top_n": 3}, "unknown_group_by"),
        ({"group_by": "product", "top_n": 3, "basis": "invoiced"}, "unknown_basis"),
        ({"group_by": "product", "top_n": 3, "measure": "profit"}, "unknown_measure"),
        ({"group_by": "product", "top_n": 3, "sort": "sideways"}, "unknown_sort"),
        ({"group_by": "product", "top_n": 0}, "top_n_out_of_range"),
        ({"group_by": "product", "top_n": 1001}, "top_n_out_of_range"),
        ({"group_by": "product", "top_n": 3, "channel": "retail"}, "invalid_channel"),
        ({"group_by": "product", "top_n": 3, "date_from": "2026-10-01", "date_to": "2026-09-01"},
         "date_range_inverted"),
    ],
)
def test_ac_re_17_validation_codes(client, db, params, code):
    resp = _ask(client, _full(db), **params)
    assert resp.status_code == 422, resp.text
    assert _code(resp) == code, resp.text


def test_contact_identity_pair_is_required(client, db):
    contact = _full(db)
    resp = client.get(ROUTE, params={**PERIOD, "contact_id": contact.id, "group_by": "product", "top_n": 3},
                      headers=KEY)
    assert resp.status_code == 422 and _code(resp) == "contact_identity_required", resp.text
    resp = client.get(ROUTE, params={**PERIOD, "space_id": "zzt-space"}, headers=KEY)
    assert resp.status_code == 422 and _code(resp) == "contact_identity_required", resp.text


def test_a_request_without_an_api_key_is_403(client, db):
    resp = _ask(client, _full(db), headers={}, group_by="product", top_n=3)
    assert resp.status_code == 403 and _code(resp) == "api_key_required", resp.text


# ================================================================ AC-RE-12: the grant


def test_ac_re_12_a_contact_without_the_grant_is_refused(client, db):
    contact = _contact(db, granted=False)
    db.commit()
    resp = _ask(client, contact, group_by="product", top_n=3)
    assert resp.status_code == 403 and _code(resp) == "sales_report_not_enabled", resp.text


def test_ac_re_12_an_unknown_contact_is_refused(client, db):
    resp = client.get(ROUTE, params={**PERIOD, "contact_id": str(uuid.uuid4()), "space_id": "zzt-space",
                                     "group_by": "product", "top_n": 3}, headers=KEY)
    assert resp.status_code == 403 and _code(resp) == "sales_report_not_enabled", resp.text


# ================================================================ AC-RE-13 / 14 / 15: audiences


def _dealer(db, w):
    contact = _contact(db)
    _link(db, contact, w.cust)
    db.commit()
    return contact


@pytest.mark.parametrize("group_by", ["sales_agent", "location", "customer", "channel"])
def test_ac_re_13_a_dealer_may_not_group_by_staff_dimensions(client, db, group_by):
    w = _ranking_world(db)
    resp = _ask(client, _dealer(db, w), group_by=group_by, top_n=3)
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == "report_dimension_not_allowed", body
    assert DEALER_MESSAGE in resp.text, body


@pytest.mark.parametrize(
    "extra",
    [
        {"sales_agent_ids": [str(uuid.uuid4())]},
        {"warehouse_codes": ["ZZT-ANY-WH"]},
        {"channel": "dealer"},
    ],
)
def test_ac_re_13_a_dealer_may_not_filter_by_staff_dimensions(client, db, extra):
    w = _ranking_world(db)
    resp = _ask(client, _dealer(db, w), group_by="product", top_n=3, **extra)
    assert resp.status_code == 403 and _code(resp) == "report_dimension_not_allowed", resp.text
    assert DEALER_MESSAGE in resp.text, resp.text


@pytest.mark.parametrize("group_by", ["product", "brand", "category", "month"])
def test_ac_re_13_a_dealer_may_group_by_product_brand_category_month(client, db, group_by):
    w = _ranking_world(db)
    body = _ok(client, _dealer(db, w), group_by=group_by, top_n=3)
    assert body["group_by"] == group_by and body["total_count"] >= 1, body


def test_ac_re_14_a_dealer_sees_only_its_own_customers_figures(client, db):
    w = _ranking_world(db)
    other = seed_customer(db, name=unique_code("Other"))
    _sale(db, cust=other, wh=w.wh, agent=w.a1, prod=w.p1, qty=500, total="50000.00")
    contact = _dealer(db, w)
    body = _ok(client, contact, group_by="product", top_n=10)
    assert body["total"]["qty"] == 70 and money(body["total"]["amount"]) == D("5250.00"), body["total"]


def test_ac_re_14_a_dealer_naming_another_customer_is_refused(client, db):
    w = _ranking_world(db)
    other = seed_customer(db, name=unique_code("Other"))
    db.commit()
    resp = _ask(client, _dealer(db, w), group_by="product", top_n=3, customer_ids=[other.id])
    assert resp.status_code == 403 and _code(resp) == "customer_not_permitted", resp.text


def test_ac_re_15_a_grant_holder_with_no_links_may_rank_sales_agents(client, db):
    _ranking_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=3)
    assert body["group_by"] == "sales_agent" and body["total_count"] == 3, body


def test_ac_re_15_an_office_contact_linked_to_a_customer_is_not_a_dealer(client, db):
    w = _ranking_world(db)
    contact = _contact(db)
    _link(db, contact, w.cust)
    _access(db, contact, "Sorento Office")
    db.commit()
    body = _ok(client, contact, group_by="sales_agent", top_n=3)
    assert body["group_by"] == "sales_agent" and body["total_count"] == 3, body


# ================================================================ AC-RE-16: company scope


def test_ac_re_16_another_companys_so_lines_are_not_counted(client, db):
    mocha = seed_mocha(db)
    shared = unique_code("ZZTCO", alpha=True)
    cust = seed_customer(db, name=unique_code("Cust"))
    mocha_cust = seed_customer(db, name=unique_code("MochaCust"), company_id=mocha.id)
    prod_a = _prod(db, shared)
    prod_b = product(db, company_id=mocha.id, code=shared)
    wh_a = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    wh_b = warehouse(db, company_id=mocha.id)
    seed_so_line(db, cust=cust, prod=prod_a, wh=wh_a, qty=10, total="100.00")
    seed_so_line(db, cust=mocha_cust, prod=prod_b, wh=wh_b, qty=500, total="5000.00", company_id=mocha.id)
    db.commit()
    body = _ok(client, _full(db), product_code=shared)
    assert body["total"]["qty"] == 10 and money(body["total"]["amount"]) == D("100.00"), body["total"]


# ================================================================ AC-RE-17: empty resolution


def test_ac_re_17_a_product_code_matching_nothing_is_404(client, db):
    resp = _ask(client, _full(db), group_by="product", top_n=3, product_code="ZZTNOSUCHPRODUCT")
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "NOT_FOUND", resp.text  # not the missing route's bare 404


def test_ac_re_17_warehouse_codes_naming_no_warehouse_is_zero_rows(client, db):
    _ranking_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=3, warehouse_codes=["ZZT-NO-SUCH-WH"])
    assert body["status"] == "ok" and body["rows"] == [] and body["total_count"] == 0, body
    assert body["total"]["qty"] == 0 and money(body["total"]["amount"]) == D("0.00"), body["total"]


# ================================================================ location policy


def test_a_named_location_outside_the_policy_is_refused(client, db):
    w = _ranking_world(db)
    barred = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-BARRED")
    contact = _contact(db)
    seed_policy(db, contact, excluded_warehouse_ids=[barred.id])
    db.commit()
    body = _ok(client, contact, group_by="sales_agent", top_n=3, warehouse_codes=["ZZT-BARRED"])
    assert body["status"] == "refused", body
    assert body["message"] == "Sorry, ZZT-BARRED isn't one of the locations you can check.", body
    assert w is not None


# ================================================================ AC-RE-20: parity with top selling
# Owner direction 4 Oct 2026: report-ask ranks from SO lines with top selling's own per-line figures
# and date, so `group_by=product` is top selling's item ranking, number for number and in order. The
# DO-based sales-report route stays on DOs and is no longer the reference.

WIN = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
AUG20 = date(2026, 8, 20)


def _parity_world(db):
    """Five products in two categories, two agents, one chain of partial deliveries.

        line  product  agent  SO date  required  status        ordered  delivered  total
        L1    PA       AG1    Sep 10   -         open/open     10       10         1000.00
        L2    PA       AG2    Sep 11   -         open/open     20       8          600.00
        L3    PB       AG1    Sep 12   -         closed/closed 10       4          500.00   (short, closed)
        L4    PC       AG2    Sep 13   -         open/cancel   99       99         9999.00  (cancelled line)
        L5    PB       AG2    Aug 20   Sep 12    open/open     6        6          120.00   (required date in Sep)
        L6    PB       AG1    Sep 14   Oct 5     open/open     50       50         5000.00  (required date in Oct)
        L7    PD       AG1    Sep 15   -         open/open     5        5          333.33   (second category)
        L8    PC       AG2    Sep 16   -         open/open     3        0          90.00    (nothing delivered)
        L9    PC       AG1    Sep 17   -         open/open     4        4          0.00     (real qty, zero value)
    """
    w = SimpleNamespace()
    w.cust = seed_customer(db, name=unique_code("Cust"))
    w.wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    w.cat1, w.cat2 = _category(db, "ZZT-PAR-CAT1"), _category(db, "ZZT-PAR-CAT2")
    w.pa, w.pb, w.pc = (_prod(db, f"ZZTPAR-{n}", category=w.cat1) for n in "ABC")
    w.pd = _prod(db, "ZZTPAR-D", category=w.cat2)
    w.ag1, w.ag2 = seed_agent(db, code="ZZT-PAR-AG1"), seed_agent(db, code="ZZT-PAR-AG2")
    kw = dict(cust=w.cust, wh=w.wh)
    seed_so_line(db, prod=w.pa, agent=w.ag1, qty=10, total="1000.00", order_date=date(2026, 9, 10), **kw)
    seed_so_line(db, prod=w.pa, agent=w.ag2, qty=20, delivered=8, total="600.00", order_date=date(2026, 9, 11), **kw)
    seed_so_line(db, prod=w.pb, agent=w.ag1, qty=10, delivered=4, total="500.00", order_date=date(2026, 9, 12),
                 so_status="closed", line_status="closed", **kw)
    seed_so_line(db, prod=w.pc, agent=w.ag2, qty=99, total="9999.00", order_date=date(2026, 9, 13),
                 line_status="cancelled", **kw)
    seed_so_line(db, prod=w.pb, agent=w.ag2, qty=6, total="120.00", order_date=AUG20,
                 required_date=date(2026, 9, 12), **kw)
    seed_so_line(db, prod=w.pb, agent=w.ag1, qty=50, total="5000.00", order_date=date(2026, 9, 14),
                 required_date=date(2026, 10, 5), **kw)
    seed_so_line(db, prod=w.pd, agent=w.ag1, qty=5, total="333.33", order_date=date(2026, 9, 15), **kw)
    seed_so_line(db, prod=w.pc, agent=w.ag2, qty=3, delivered=0, total="90.00", order_date=date(2026, 9, 16), **kw)
    seed_so_line(db, prod=w.pc, agent=w.ag1, qty=4, total="0.00", order_date=date(2026, 9, 17), **kw)
    db.commit()
    return w


def _top_selling_rows(db, *, rank_by, basis, **extra):
    from app.models.base import set_company_scope
    from app.services.sales_report_service import top_selling

    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    out = top_selling(db, rank_by=rank_by, basis=basis, group="item", date_from=date(2026, 9, 1),
                      date_to=date(2026, 9, 30), **extra)
    return [(r["code"], r["quantity"], money(r["amount"])) for r in out["rows"]], out


@pytest.mark.parametrize("basis", ["delivered", "ordered"])
@pytest.mark.parametrize("measure, rank_by", [("amount", "amount"), ("qty", "quantity")])
def test_ac_re_20_group_by_product_is_top_sellings_item_ranking(client, db, basis, measure, rank_by):
    """Same per-product numbers AND the same order as `top_selling`, on both bases and both measures."""
    _parity_world(db)
    ask = _ok(client, _full(db), group_by="product", top_n=100, basis=basis, measure=measure)
    expected, out = _top_selling_rows(db, rank_by=rank_by, basis=basis)
    assert len(expected) >= 3, expected  # the world ranks several products
    assert _rows(ask) == expected, (basis, measure, _rows(ask), expected)
    assert ask["total_count"] == out["total_count"], (ask["total_count"], out["total_count"])
    assert ask["total"]["qty"] == out["totals"]["quantity"], (ask["total"], out["totals"])
    assert money(ask["total"]["amount"]) == money(out["totals"]["amount"]), (ask["total"], out["totals"])


def test_ac_re_20_the_world_pins_the_numbers_not_only_the_parity(client, db):
    """Hand-computed, so two wrong-in-the-same-way implementations cannot agree. Delivered, by amount:
    PA 10+8 / 1000+240, PB 4+6 / 200+120, PD 5 / 333.33, PC 4 / 0.00 (qty, no value). The cancelled
    line, the Oct-required line and the undelivered PC line are out or zero."""
    _parity_world(db)
    contact = _full(db)
    delivered = _ok(client, contact, group_by="product", top_n=100, basis="delivered")
    assert _rows(delivered) == [("ZZTPAR-A", 18, D("1240.00")), ("ZZTPAR-D", 5, D("333.33")),
                                ("ZZTPAR-B", 10, D("320.00")), ("ZZTPAR-C", 4, D("0.00"))], delivered["rows"]
    ordered = _ok(client, contact, group_by="product", top_n=100, basis="ordered")
    # PA 30 / 1600, PB 10+6 / 500+120, PD 5 / 333.33, PC 3+4 / 90
    assert _rows(ordered) == [("ZZTPAR-A", 30, D("1600.00")), ("ZZTPAR-B", 16, D("620.00")),
                              ("ZZTPAR-D", 5, D("333.33")), ("ZZTPAR-C", 7, D("90.00"))], ordered["rows"]


@pytest.mark.parametrize("basis", ["delivered", "ordered"])
@pytest.mark.parametrize("measure, rank_by", [("amount", "amount"), ("qty", "quantity")])
def test_ac_re_20_with_a_category_filter_it_still_equals_top_selling(client, db, basis, measure, rank_by):
    w = _parity_world(db)
    ask = _ok(client, _full(db), group_by="product", top_n=100, basis=basis, measure=measure,
              category_ids=[w.cat1.id])
    expected, out = _top_selling_rows(db, rank_by=rank_by, basis=basis, category_ids=[w.cat1.id])
    assert "ZZTPAR-D" not in [name for name, _q, _a in expected], expected  # the other category is out
    assert _rows(ask) == expected, (basis, measure, _rows(ask), expected)
    assert ask["total"]["qty"] == out["totals"]["quantity"], (ask["total"], out["totals"])
    assert money(ask["total"]["amount"]) == money(out["totals"]["amount"]), (ask["total"], out["totals"])


def test_ac_re_20_group_by_category_equals_top_sellings_category_ranking(client, db):
    from app.models.base import set_company_scope
    from app.services.sales_report_service import top_selling

    _parity_world(db)
    ask = _ok(client, _full(db), group_by="category", top_n=100, basis="delivered")
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    out = top_selling(db, rank_by="amount", basis="delivered", group="category",
                      date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
    # top selling names a category by its code, report-ask by its name; compare the figures in order.
    assert [(r["qty"], money(r["amount"])) for r in ask["rows"]] == [
        (r["quantity"], money(r["amount"])) for r in out["rows"]], (ask["rows"], out["rows"])


# ================================================================ REPORT-ENGINE: every basis is SO lines


def test_every_basis_ranks_the_sales_agent_of_the_so_not_of_any_delivery(client, db):
    """No DO exists anywhere in this chain: the delivered basis still ranks, by the SO's own agent."""
    w = _parity_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, basis="delivered")
    # AG1: L1 10/1000 + L3 4/200 + L7 5/333.33 + L9 4/0 ; AG2: L2 8/240 + L5 6/120 (L8 delivered nothing)
    assert _rows(body) == [("ZZT-PAR-AG1", 23, D("1533.33")), ("ZZT-PAR-AG2", 14, D("360.00"))], body["rows"]
    assert w.ag1.sales_agent == "ZZT-PAR-AG1"


def test_the_delivered_basis_dates_a_line_by_its_required_date(client, db):
    """`group_by=month` is bucketed by `coalesce(required_date, order_date)`: L5 (Aug SO, required
    Sep 12) is September; the Oct-required L6 is outside a September window and absent."""
    _parity_world(db)
    body = _ok(client, _full(db), group_by="month", top_n=12, date_from="2026-08-01", date_to="2026-09-30")
    assert [r["name"] for r in body["rows"]] == ["2026-09"], body["rows"]
    assert body["rows"][0]["qty"] == 37, body["rows"]
    assert money(body["rows"][0]["amount"]) == D("1893.33"), body["rows"]
    wide = _ok(client, _full(db), group_by="month", top_n=12, date_from="2026-08-01", date_to="2026-10-31")
    assert sorted(r["name"] for r in wide["rows"]) == ["2026-09", "2026-10"], wide["rows"]


def test_a_cancelled_so_and_a_cancelled_line_are_out_of_every_basis(client, db):
    cust = seed_customer(db, name=unique_code("Cust"))
    prod = _prod(db, "ZZTCAN-1")
    keep, gone = seed_agent(db, code="ZZT-CAN-KEEP"), seed_agent(db, code="ZZT-CAN-GONE")
    seed_so_line(db, cust=cust, prod=prod, agent=keep, qty=1, total="10.00")
    seed_so_line(db, cust=cust, prod=prod, agent=gone, qty=5, total="50.00", so_status="cancelled")
    seed_so_line(db, cust=cust, prod=prod, agent=gone, qty=6, total="60.00", line_status="cancelled")
    db.commit()
    contact = _full(db)
    for basis in ("delivered", "ordered", "outstanding"):
        body = _ok(client, contact, group_by="sales_agent", top_n=10, basis=basis)
        assert "ZZT-CAN-GONE" not in [r["name"] for r in body["rows"]], (basis, body["rows"])


def test_an_agent_with_nothing_delivered_is_not_ranked_on_the_delivered_basis(client, db):
    """Top selling's zero-group rule: qty AND amount both 0 on the basis means no row."""
    cust = seed_customer(db, name=unique_code("Cust"))
    prod = _prod(db, "ZZTZERO-1")
    sold, waiting = seed_agent(db, code="ZZT-ZERO-SOLD"), seed_agent(db, code="ZZT-ZERO-WAIT")
    seed_so_line(db, cust=cust, prod=prod, agent=sold, qty=2, total="20.00")
    seed_so_line(db, cust=cust, prod=prod, agent=waiting, qty=9, total="90.00", delivered=0)
    db.commit()
    contact = _full(db)
    delivered = _ok(client, contact, group_by="sales_agent", top_n=10, basis="delivered")
    assert [r["name"] for r in delivered["rows"]] == ["ZZT-ZERO-SOLD"], delivered["rows"]
    ordered = _ok(client, contact, group_by="sales_agent", top_n=10, basis="ordered")
    assert [r["name"] for r in ordered["rows"]] == ["ZZT-ZERO-WAIT", "ZZT-ZERO-SOLD"], ordered["rows"]


# ================================================================ REPORT-ENGINE: the outstanding basis


def _outstanding_world(db):
    """Per agent, the lines and what is still owed (open SO AND open line only).

        agent  line                                         outstanding qty / value
        O1     ordered 10 delivered 4 total 1000.00, open   6 / 600.00 (1000.00 less 400.00 delivered)
        O1     ordered 5 delivered 0 total 50.00, open      5 / 50.00                  -> O1 11 / 650.00
        O2     SO closed, line closed, 10 ordered, 2 sent   0 (closed: nothing owed)   -> not ranked
        O3     fully delivered, open                        0                          -> not ranked
        O4     SO open, LINE closed, 8 ordered, 0 sent      0                          -> not ranked
        O5     SO cancelled                                 out
        O6     ordered 3, delivered 5 (over delivered)      0                          -> not ranked
        O7     ordered 20 delivered 0 total 200.00, open    20 / 200.00
        O8     Aug SO, required Sep 12, 1 ordered, open     1 / 10.00 (dated by its required date)
        O9     Sep SO, required Oct 5, open                 out of a September window
    """
    cust = seed_customer(db, name=unique_code("Cust"))
    prod = _prod(db, "ZZTOUT-1")
    ag = {n: seed_agent(db, code=f"ZZT-OUT-{n}") for n in range(1, 10)}
    kw = dict(cust=cust, prod=prod)
    seed_so_line(db, agent=ag[1], qty=10, delivered=4, total="1000.00", **kw)
    seed_so_line(db, agent=ag[1], qty=5, delivered=0, total="50.00", **kw)
    seed_so_line(db, agent=ag[2], qty=10, delivered=2, total="100.00", so_status="closed", line_status="closed", **kw)
    seed_so_line(db, agent=ag[3], qty=7, total="70.00", **kw)
    seed_so_line(db, agent=ag[4], qty=8, delivered=0, total="80.00", line_status="closed", **kw)
    seed_so_line(db, agent=ag[5], qty=9, delivered=0, total="90.00", so_status="cancelled", **kw)
    seed_so_line(db, agent=ag[6], qty=3, delivered=5, total="30.00", **kw)
    seed_so_line(db, agent=ag[7], qty=20, delivered=0, total="200.00", **kw)
    seed_so_line(db, agent=ag[8], qty=1, delivered=0, total="10.00", order_date=AUG, required_date=date(2026, 9, 12), **kw)
    seed_so_line(db, agent=ag[9], qty=40, delivered=0, total="400.00", required_date=date(2026, 10, 5), **kw)
    db.commit()
    return ag


def test_outstanding_basis_ranks_what_is_still_owed_on_open_orders(client, db):
    _outstanding_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, basis="outstanding")
    assert body["basis"] == "outstanding", body
    assert _rows(body) == [("ZZT-OUT-1", 11, D("650.00")), ("ZZT-OUT-7", 20, D("200.00")),
                           ("ZZT-OUT-8", 1, D("10.00"))], body["rows"]
    assert body["total_count"] == 3 and body["more"] == 0, body
    assert body["total"]["qty"] == 32 and money(body["total"]["amount"]) == D("860.00"), body["total"]


def test_outstanding_basis_measure_qty_ranks_by_quantity(client, db):
    _outstanding_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, basis="outstanding", measure="qty")
    assert [(r["name"], r["qty"]) for r in body["rows"]] == [
        ("ZZT-OUT-7", 20), ("ZZT-OUT-1", 11), ("ZZT-OUT-8", 1)], body["rows"]


def test_an_agent_whose_lines_are_all_delivered_is_not_in_an_outstanding_ranking(client, db):
    ag = _outstanding_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, basis="outstanding")
    names = {r["name"] for r in body["rows"]}
    for n in (2, 3, 4, 5, 6, 9):
        assert ag[n].sales_agent not in names, (n, names)
    assert body["total_count"] == 3, body


def test_the_outstanding_header_label_reads_outstanding_orders(client, db):
    _outstanding_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=3, basis="outstanding")
    assert body["basis_label"] == "outstanding orders", body["basis_label"]
    # The other two labels are untouched.
    assert _ok(client, _full(db), basis="delivered")["basis_label"] == "delivered sales"
    assert _ok(client, _full(db), basis="ordered")["basis_label"] == "ordered sales"


def test_the_outstanding_header_reads_top_n_sales_agents_by_outstanding_orders(client, db):
    """The MCP presenter prints the route's `basis_label`; the whole header is pinned here so a
    renamed label cannot slip past both repositories."""
    _outstanding_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=3, basis="outstanding")
    from sorento_crm_mcp.presenters import _report_ask

    first = _report_ask(body).split("\n")[0]
    assert first.startswith("Top 3 sales agents by outstanding orders, "), first


def test_outstanding_works_with_a_total_and_a_filter(client, db):
    ag = _outstanding_world(db)
    body = _ok(client, _full(db), basis="outstanding", sales_agent_ids=[ag[1].id])
    assert body["group_by"] is None and body["rows"] == [], body
    assert body["total"]["qty"] == 11 and money(body["total"]["amount"]) == D("650.00"), body["total"]


def test_outstanding_is_an_allowed_basis_in_the_unknown_basis_detail(client, db):
    resp = _ask(client, _full(db), group_by="product", top_n=3, basis="invoiced")
    assert resp.status_code == 422 and _code(resp) == "unknown_basis", resp.text
    assert "outstanding" in str(resp.json().get("detail")), resp.text


def test_a_dealer_may_ask_the_outstanding_basis_for_its_own_customer_only(client, db):
    w = _ranking_world(db)
    other = seed_customer(db, name=unique_code("Other"))
    seed_so_line(db, cust=other, prod=w.p1, agent=w.a1, qty=500, total="50000.00", delivered=0)
    seed_so_line(db, cust=w.cust, prod=w.p1, agent=w.a1, qty=3, total="30.00", delivered=0)
    db.commit()
    body = _ok(client, _dealer(db, w), group_by="product", top_n=10, basis="outstanding")
    assert body["total"]["qty"] == 3 and money(body["total"]["amount"]) == D("30.00"), body["total"]


def test_top_selling_does_not_accept_outstanding_as_its_basis(client, db):
    """Guard: `outstanding` is report-ask's basis only. Top selling keeps delivered | ordered."""
    resp = client.get("/api/v1/order-management/top-selling",
                      params={**PERIOD, "rank_by": "quantity", "basis": "outstanding"})
    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "invalid_basis", resp.text


# ================================================================ REPORT-ENGINE: the default basis


def test_the_default_basis_is_delivered_when_basis_is_omitted(client, db):
    """PENDING OWNER DECISION: the default is one constant and this pins today's value. A change
    of default is a one-line edit here and in the route's single constant, nowhere else."""
    _ranking_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=3)
    assert body["basis"] == "delivered" and body["basis_label"] == "delivered sales", body


def test_the_routes_declared_default_basis_is_delivered(client, db):
    spec = client.get("/openapi.json").json()
    params = spec["paths"][ROUTE]["get"]["parameters"]
    (basis,) = [p for p in params if p["name"] == "basis"]
    assert basis["schema"].get("default") == "delivered", basis


# ================================================================ fix round 1 (REPORT-ENGINE)


def _tie_world(db, specs):
    """One agent per (code, qty, total), one fully delivered SO each, all on one product."""
    cust = seed_customer(db, name=unique_code("Cust"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    prod = _prod(db, unique_code("ZZTTIE", alpha=True))
    for code, qty, total in specs:
        _sale(db, cust=cust, wh=wh, agent=seed_agent(db, code=code), prod=prod, qty=qty, total=total)
    db.commit()


def _order(body):
    return [r["name"] for r in body["rows"]]


@pytest.mark.parametrize("sort, expected", [("desc", ["ZZT-T2", "ZZT-T1"]), ("asc", ["ZZT-T1", "ZZT-T2"])])
def test_ac_re_6_amount_tie_breaks_on_qty_in_the_sort_direction(client, db, sort, expected):
    """AC-RE-6 / reviewer S1: equal amount, qty 5 vs 9: desc puts the higher qty first, asc the lower."""
    _tie_world(db, [("ZZT-T1", 5, "100.00"), ("ZZT-T2", 9, "100.00")])
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, sort=sort)
    assert _order(body) == expected, body["rows"]


@pytest.mark.parametrize("sort", ["desc", "asc"])
def test_ac_re_6_full_tie_breaks_on_name_ascending_both_ways(client, db, sort):
    """AC-RE-6 / S1: equal amount AND qty: name ascending whichever way the sort runs."""
    _tie_world(db, [("ZZT-T4", 2, "50.00"), ("ZZT-T3", 2, "50.00")])
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, sort=sort)
    assert _order(body) == ["ZZT-T3", "ZZT-T4"], body["rows"]


@pytest.mark.parametrize("sort, expected", [("desc", ["ZZT-T2", "ZZT-T1"]), ("asc", ["ZZT-T1", "ZZT-T2"])])
def test_ac_re_6_qty_tie_breaks_on_amount_in_the_sort_direction(client, db, sort, expected):
    """AC-RE-6 / S1: measure=qty, equal qty: the other measure (amount) breaks the tie."""
    _tie_world(db, [("ZZT-T1", 5, "100.00"), ("ZZT-T2", 5, "300.00")])
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, sort=sort, measure="qty")
    assert _order(body) == expected, body["rows"]


@pytest.mark.parametrize("sort", ["desc", "asc"])
def test_ac_re_6_qty_full_tie_breaks_on_name_ascending(client, db, sort):
    _tie_world(db, [("ZZT-T4", 5, "100.00"), ("ZZT-T3", 5, "100.00")])
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, sort=sort, measure="qty")
    assert _order(body) == ["ZZT-T3", "ZZT-T4"], body["rows"]


@pytest.mark.parametrize(
    "param", ["brand_ids", "category_ids", "sales_agent_ids", "customer_ids", "warehouse_codes", "product_code"]
)
def test_b2_a_blank_filter_is_422_empty_filter_never_no_filter(client, db, param):
    """B2: a filter param present but blank must not widen to "no filter"."""
    _ranking_world(db)
    value = "   " if param == "product_code" else ""
    resp = _ask(client, _full(db), group_by="sales_agent", top_n=3, **{param: value})
    assert resp.status_code == 422, resp.text
    assert _code(resp) == "empty_filter", resp.text
    assert param in str(resp.json().get("detail")), resp.text


@pytest.mark.parametrize("param", ["brand_ids", "category_ids", "sales_agent_ids", "customer_ids"])
def test_b2_an_id_naming_no_row_is_404_not_found(client, db, param):
    _ranking_world(db)
    resp = _ask(client, _full(db), group_by="sales_agent", top_n=3, **{param: [str(uuid.uuid4())]})
    assert resp.status_code == 404 and _code(resp) == "NOT_FOUND", resp.text


def test_an_unknown_query_key_is_422_unknown_param(client, db):
    resp = _ask(client, _full(db), group_by="sales_agent", top_n=3, brand="SORENTO")
    assert resp.status_code == 422 and _code(resp) == "unknown_param", resp.text
    assert "brand" in str(resp.json().get("detail")), resp.text


@pytest.mark.parametrize(
    "params, code",
    [
        ({"group_by": "colour", "top_n": 3}, "unknown_group_by"),
        ({"group_by": "product", "top_n": 3, "basis": "invoiced"}, "unknown_basis"),
        ({"group_by": "product", "top_n": 3, "measure": "profit"}, "unknown_measure"),
        ({"group_by": "product", "top_n": 3, "sort": "sideways"}, "unknown_sort"),
        ({"group_by": "product", "top_n": 3, "channel": "retail"}, "invalid_channel"),
    ],
)
def test_enum_422s_carry_the_allowed_list_in_detail(client, db, params, code):
    resp = _ask(client, _full(db), **params)
    assert resp.status_code == 422 and _code(resp) == code, resp.text
    detail = str(resp.json().get("detail"))
    assert detail.startswith("allowed: "), resp.text
    if code == "unknown_group_by":
        assert "sales_agent" in detail, resp.text


def test_a_list_param_over_50_values_is_422_too_many_values(client, db):
    resp = _ask(client, _full(db), group_by="product", top_n=3, brand_ids=[str(uuid.uuid4()) for _ in range(51)])
    assert resp.status_code == 422 and _code(resp) == "too_many_values", resp.text
    assert "brand_ids" in str(resp.json().get("detail")), resp.text


def test_exactly_50_values_is_not_too_many(client, db):
    resp = _ask(client, _full(db), group_by="product", top_n=3, brand_ids=[str(uuid.uuid4()) for _ in range(50)])
    assert _code(resp) != "too_many_values", resp.text


@pytest.mark.parametrize("params", [{"date_to": "9999-12-31"}, {"date_from": "1800-01-01"}])
def test_a_year_outside_1900_to_2200_is_422(client, db, params):
    resp = _ask(client, _full(db), group_by="product", top_n=3, **params)
    assert resp.status_code == 422 and _code(resp) == "date_out_of_range", resp.text


def _only_company(db, contact, company_id):
    """Make `company_id` the contact's ONLY company (replacing the default one `_contact` gave it)."""
    from app.models.company import RespondContactCompany

    db.query(RespondContactCompany).filter(RespondContactCompany.respond_contact_id == contact.id).delete()
    db.add(RespondContactCompany(id=str(uuid.uuid4()), respond_contact_id=contact.id, company_id=company_id))
    db.commit()


def test_s1_the_company_grant_comes_from_the_contact_not_the_session_scope(client, db):
    """Security S1/I3: a contact whose only company is the second one sees that company's SO lines and
    none of the default company's, though the client session scope is the default company."""
    mocha = seed_mocha(db)
    cust = seed_customer(db, name=unique_code("Cust"))
    mocha_cust = seed_customer(db, name=unique_code("MochaCust"), company_id=mocha.id)
    prod = _prod(db, unique_code("ZZTGR", alpha=True))
    prod_m = product(db, company_id=mocha.id, code=unique_code("ZZTGM", alpha=True))
    wh, wh_m = warehouse(db, company_id=DEFAULT_COMPANY_ID), warehouse(db, company_id=mocha.id)
    seed_so_line(db, cust=cust, prod=prod, wh=wh, qty=99, total="999.00")
    seed_so_line(db, cust=mocha_cust, prod=prod_m, wh=wh_m, qty=10, total="100.00", company_id=mocha.id)
    contact = _contact(db)
    _only_company(db, contact, mocha.id)
    body = _ok(client, contact)
    assert body["total"]["qty"] == 10 and money(body["total"]["amount"]) == D("100.00"), body["total"]


def _ordered_in(db, *, company_id, cust, prod, qty, total):
    so = SalesOrder(id=str(uuid.uuid4()), so_number=unique_code("SO"), customer_id=cust.id,
                    order_date=SEP, status="open", company_id=company_id)
    db.add(so)
    db.flush()
    db.add(SalesOrderLine(id=str(uuid.uuid4()), sales_order_id=so.id, product_id=prod.id,
                          qty_ordered=qty, qty_delivered=0, line_total=D(total), line_status="open",
                          company_id=company_id))
    db.flush()


def _ordered_two_companies(db):
    mocha = seed_mocha(db)
    cust = seed_customer(db, name=unique_code("Cust"))
    mocha_cust = seed_customer(db, name=unique_code("MochaCust"), company_id=mocha.id)
    prod = _prod(db, unique_code("ZZTOR", alpha=True))
    prod_m = product(db, company_id=mocha.id, code=unique_code("ZZTOM", alpha=True))
    _ordered_in(db, company_id=DEFAULT_COMPANY_ID, cust=cust, prod=prod, qty=99, total="999.00")
    _ordered_in(db, company_id=mocha.id, cust=mocha_cust, prod=prod_m, qty=10, total="100.00")
    return mocha


def test_s1_ordered_basis_does_not_count_another_companys_so_lines(client, db):
    _ordered_two_companies(db)
    contact = _full(db)  # default company only
    body = _ok(client, contact, basis="ordered")
    assert body["total"]["qty"] == 99 and money(body["total"]["amount"]) == D("999.00"), body["total"]


def test_s1_ordered_basis_reads_the_contacts_own_company(client, db):
    mocha = _ordered_two_companies(db)
    contact = _contact(db)
    _only_company(db, contact, mocha.id)
    body = _ok(client, contact, basis="ordered")
    assert body["total"]["qty"] == 10 and money(body["total"]["amount"]) == D("100.00"), body["total"]


def test_s1_a_whitespace_space_id_is_contact_identity_required(client, db):
    contact = _full(db)
    resp = client.get(ROUTE, params={**PERIOD, "contact_id": contact.id, "space_id": " "}, headers=KEY)
    assert resp.status_code == 422 and _code(resp) == "contact_identity_required", resp.text


def test_a_rate_limited_ask_answers_busy_through_the_response_model(client, db, monkeypatch):
    from app.schemas.report_ask import ReportAskResponse
    from app.services import rate_limit

    monkeypatch.setattr(rate_limit, "hit", lambda *a, **k: SimpleNamespace(allowed=False))
    body = _ok(client, _full(db), group_by="sales_agent", top_n=3)
    assert body["status"] == "busy" and body["rows"] == [], body
    assert body["basis_label"] == "delivered sales", body
    ReportAskResponse(**body)  # every declared field is present and well typed


def test_a_dealer_naming_its_own_customer_is_echoed(client, db):
    w = _ranking_world(db)
    body = _ok(client, _dealer(db, w), group_by="product", top_n=3, customer_ids=[w.cust.id])
    names = [f["values"] for f in body["filters"] if f["key"] == "customer"]
    assert names == [[w.cust.customer_name]], body["filters"]


def test_s2_run_ask_with_an_empty_filter_list_returns_zero_rows(db):
    """Reviewer S2: a filter that resolved to nothing is zero rows, never "no filter"."""
    from datetime import date as _date

    from app.services.reports import ask

    _ranking_world(db)
    def _run(filters):
        return ask.run_ask(
            db, basis="delivered", measure="amount", group_by="sales_agent",
            date_from=_date(2026, 9, 1), date_to=_date(2026, 9, 30),
            filters=filters, warehouse_codes=[],
            policy=SimpleNamespace(warehouse_ids=None, excluded_warehouse_ids=None), top_n=10,
            company_grants=[DEFAULT_COMPANY_ID],
        )

    # Control (re-review N1): with a real company grant the same run HAS rows, so the empty
    # brand list below is zero rows because of the guard, not because of the company arm.
    assert _run({})["total_count"] > 0
    out = _run({"brand": []})
    assert out["rows"] == [] and out["total_count"] == 0, out
    assert out["total"]["qty"] == 0 and money(out["total"]["amount"]) == D("0.00"), out


def test_a_contact_with_no_company_cannot_read_names_through_the_echo(client, db):
    """Re-review S1-new: with no company of its own, a named id is outside every company the
    contact holds, so it is 404 and its name never comes back."""
    from app.models.company import RespondContactCompany

    mocha = seed_mocha(db)
    secret = seed_customer(db, name="ZZT SECRET MOCHA CUST", company_id=mocha.id)
    contact = _contact(db)
    db.query(RespondContactCompany).filter(RespondContactCompany.respond_contact_id == contact.id).delete()
    db.commit()
    resp = _ask(client, contact, customer_ids=[secret.id])
    assert resp.status_code == 404, resp.text
    assert "ZZT SECRET MOCHA CUST" not in resp.text


def test_product_code_is_matched_inside_the_contacts_companies(client, db):
    """Re-review S2-new: the product prefix resolves under the CONTACT's companies, not the
    session scope (the test client's session is the default company)."""
    mocha = seed_mocha(db)
    mocha_cust = seed_customer(db, name=unique_code("MochaCust"), company_id=mocha.id)
    code = unique_code("ZZTPM", alpha=True)
    prod_m = product(db, company_id=mocha.id, code=code)
    wh_m = warehouse(db, company_id=mocha.id)
    seed_so_line(db, cust=mocha_cust, prod=prod_m, wh=wh_m, qty=7, total="70.00", company_id=mocha.id)
    contact = _contact(db)
    _only_company(db, contact, mocha.id)
    body = _ok(client, contact, product_code=code)
    assert body["total"]["qty"] == 7 and money(body["total"]["amount"]) == D("70.00"), body["total"]


# ================================================================ slice 1b: product_ids


def test_1b_product_ids_counts_only_the_named_products(client, db):
    w = _ranking_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, product_ids=[w.p1.id])
    assert _rows(body) == [("ZZT-AG-1", 10, D("1000.00")), ("ZZT-AG-2", 20, D("600.00"))], body["rows"]
    assert money(body["total"]["amount"]) == D("1600.00") and body["total"]["qty"] == 30, body["total"]


def test_1b_product_ids_and_product_code_are_anded(client, db):
    w = _ranking_world(db)
    # ids = p1 and p3; the prefix ZZTBB names only p3: the AND leaves p3.
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, product_ids=[w.p1.id, w.p3.id],
               product_code="ZZTBB")
    assert _rows(body) == [("ZZT-AG-3", 30, D("3000.00")), ("ZZT-AG-1", 5, D("500.00"))], body["rows"]


def test_1b_blank_product_ids_is_422_empty_filter(client, db):
    _ranking_world(db)
    resp = _ask(client, _full(db), group_by="sales_agent", top_n=3, product_ids="")
    assert resp.status_code == 422 and _code(resp) == "empty_filter", resp.text
    assert "product_ids" in str(resp.json().get("detail")), resp.text


def test_1b_an_unknown_product_id_is_404(client, db):
    _ranking_world(db)
    resp = _ask(client, _full(db), group_by="sales_agent", top_n=3, product_ids=[str(uuid.uuid4())])
    assert resp.status_code == 404 and _code(resp) == "NOT_FOUND", resp.text


def test_1b_more_than_50_product_ids_is_too_many_values(client, db):
    resp = _ask(client, _full(db), group_by="product", top_n=3, product_ids=[str(uuid.uuid4()) for _ in range(51)])
    assert resp.status_code == 422 and _code(resp) == "too_many_values", resp.text
    assert "product_ids" in str(resp.json().get("detail")), resp.text


def test_f2d_product_ids_of_another_companys_real_product_is_404(client, db):
    """Security F2: a product that exists, but in a company outside the contact's, is not found."""
    _ranking_world(db)
    mocha = seed_mocha(db)
    foreign = product(db, company_id=mocha.id, code=unique_code("ZZTFOR", alpha=True))
    resp = _ask(client, _full(db), group_by="sales_agent", top_n=3, product_ids=[foreign.id])
    assert resp.status_code == 404 and _code(resp) == "NOT_FOUND", resp.text


# ================================================================ REPORT-ENGINE: "remove the cap"
# Owner, 30 Sep 2026: ONE ceiling, `sales_report_service.TOP_SELLING_N_CEILING` (#1407). The route no
# longer stops at 100. The constant is imported, never copied, so a second limit cannot creep back.


def _ceiling_forms() -> tuple[str, str]:
    from app.services.sales_report_service import TOP_SELLING_N_CEILING

    return str(TOP_SELLING_N_CEILING), f"{TOP_SELLING_N_CEILING:,}"


def test_top_n_200_is_accepted_and_returns_every_row_that_exists(client, db):
    """Above the old 100 cap: 200 is a valid top_n; the 3 agents of the world come back, nothing more."""
    _ranking_world(db)
    resp = _ask(client, _full(db), group_by="sales_agent", top_n=200)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok", body
    assert [r["name"] for r in body["rows"]] == ["ZZT-AG-3", "ZZT-AG-1", "ZZT-AG-2"], body["rows"]
    assert body["more"] == 0 and body["total_count"] == 3, body


def test_top_n_at_the_ceiling_is_accepted(client, db):
    from app.services.sales_report_service import TOP_SELLING_N_CEILING

    _ranking_world(db)
    resp = _ask(client, _full(db), group_by="sales_agent", top_n=TOP_SELLING_N_CEILING)
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_count"] == 3, resp.text


def test_top_n_past_the_ceiling_is_422_and_the_message_names_the_ceiling(client, db):
    from app.services.sales_report_service import TOP_SELLING_N_CEILING

    resp = _ask(client, _full(db), group_by="sales_agent", top_n=TOP_SELLING_N_CEILING + 1)
    assert resp.status_code == 422 and _code(resp) == "top_n_out_of_range", resp.text
    plain, grouped = _ceiling_forms()
    message = str(resp.json().get("message") or "")
    assert plain in message or grouped in message, message
    assert "100 " not in message.replace(plain, "").replace(grouped, ""), f"the old cap is still named: {message}"


# ================================================================ REPORT-ENGINE: the cell cap cannot truncate a ranking
# Guard test: `run_summary` runs `_pivot(cap=False)`, the SQL has no LIMIT and ranking + the top_n cut
# happen in Python over every group, so 5001+ products must still rank correctly. It is EXPECTED to pass
# on the current code; it pins the property so a later LIMIT or a pivot cap cannot slip in unseen.

BULK_PRODUCTS = 5001  # one more than the 5000-cell cap


def _bulk_world(db):
    """5001 products with one 1.00 SO line each (codes `ZZT-BULK-00000`...), plus `ZZZ-TOP` at 500.00
    whose code sorts LAST under any natural ordering. Core inserts, so it stays fast."""
    from app.models.order import SalesOrder, SalesOrderLine
    from app.models.product import Product

    cust = seed_customer(db, name=unique_code("Cust"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    seed = _prod(db, "ZZT-BULK-SEED")  # a real category + uom to hang the bulk rows on
    products = [
        {"id": str(uuid.uuid4()), "product_code": f"ZZT-BULK-{n:05d}", "product_name": f"ZZT-BULK-{n:05d}",
         "category_id": seed.category_id, "base_uom_id": seed.base_uom_id, "list_price": 0, "is_active": True,
         "company_id": DEFAULT_COMPANY_ID}
        for n in range(BULK_PRODUCTS)
    ]
    top = {"id": str(uuid.uuid4()), "product_code": "ZZZ-TOP", "product_name": "ZZZ-TOP",
           "category_id": seed.category_id, "base_uom_id": seed.base_uom_id, "list_price": 0, "is_active": True,
           "company_id": DEFAULT_COMPANY_ID}
    db.execute(Product.__table__.insert(), [*products, top])
    so_id = str(uuid.uuid4())
    db.execute(
        SalesOrder.__table__.insert(),
        [{"id": so_id, "so_number": unique_code("SO"), "customer_id": cust.id, "order_date": SEP,
          "status": "open", "company_id": DEFAULT_COMPANY_ID}],
    )

    def _line(product_id, qty, total):
        return {"id": str(uuid.uuid4()), "sales_order_id": so_id, "product_id": product_id,
                "warehouse_id": wh.id, "qty_ordered": qty, "qty_delivered": qty, "line_total": total,
                "line_status": "open", "company_id": DEFAULT_COMPANY_ID}

    db.execute(
        SalesOrderLine.__table__.insert(),
        [_line(top["id"], 5, D("500.00")), *[_line(p["id"], 1, D("1.00")) for p in products]],
    )
    db.commit()
    return top


def test_a_ranking_over_more_than_5000_groups_is_not_truncated(client, db):
    top = _bulk_world(db)
    body = _ok(client, _full(db), group_by="product", top_n=3)
    assert body["status"] == "ok", body
    groups = BULK_PRODUCTS + 1
    assert body["rows"][0]["name"] == "ZZZ-TOP" and money(body["rows"][0]["amount"]) == D("500.00"), body["rows"]
    assert body["rows"][0]["qty"] == 5, body["rows"]
    assert [r["name"] for r in body["rows"][1:]] == ["ZZT-BULK-00000", "ZZT-BULK-00001"], body["rows"]
    assert body["total_count"] == groups, (body["total_count"], groups)
    assert body["more"] == groups - 3, body["more"]
    assert money(body["total"]["amount"]) == D("500.00") + D(BULK_PRODUCTS), body["total"]
    assert body["total"]["qty"] == 5 + BULK_PRODUCTS, body["total"]
    assert top["product_code"] == "ZZZ-TOP"
