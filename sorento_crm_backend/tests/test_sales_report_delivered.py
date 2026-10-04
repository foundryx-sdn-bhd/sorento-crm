"""Phase 2 RED tests - the delivered-basis sales report (lane SALES-REPORT, PR #1401).

`documentation/plans/chatbot/selfref-scope-acceptance-criteria.md` "Sales report v4"
(AC-SR-20 to AC-SR-26), `documentation/plans/chatbot/PLAN-chatbot-selfref-scope-30sep.md`
"Design note: one dimension/measure model", mock `documentation/mockups/sales-report/index.html`.

Written BEFORE `app/services/reports/datasets/delivery_order_lines.py` or the route's new body
exist. Every route test hits `GET /api/v1/order-management/sales-report` over HTTP and must
fail on the missing key / param / module, never on a fixture error. Postgres only
(`tests/_pg_fixture.py`), every row seeded here (`tests/_sales_report_do_seed.py`); CI's
database has none.

A DO is `orders` + `order_lines`: a legacy import (`source_book` NULL) repeats the DOC total on
every line's `total`, an AutoCount DO (`source_book == "db1"`) carries its own line totals.

Ambiguities the tester did NOT resolve by guessing (flagged in the report to the captain):
* the strictness of "every grouping sums to the Total to the sen" (pinned here as EXACT, so a
  DO total that does not split evenly must be allocated line by line, last sen to a line);
* the `channel` route param vocabulary (existing `dealer|project`; the body/reply says Retail);
* the row `name` for a DO with no sales order under `group_by=sales_agent`.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.outstanding_report_service import _customer_echo

from tests._sales_report_do_seed import (  # noqa: F401  (client / db are fixtures)
    BASE,
    _as_contact,
    _contact,
    _link,
    client,
    db,
    line,
    money,
    product,
    seed_agent,
    seed_customer,
    seed_do,
    seed_policy,
    seed_segment,
    seed_so,
    warehouse,
)
from tests._mc_lookup_seed import seed_mocha
from tests._pg_fixture import unique_code

D = Decimal


def _get(client, **params):
    resp = client.get(BASE, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _world(db, *, name=None):
    """One account, one product, one warehouse."""
    cust = seed_customer(db, name=name or unique_code("Cust"))
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    return cust, prod, wh


# ============================================================ AC-SR-20 / AC-SR-22: the dataset


def test_dataset_catalog_dimensions_and_measures():
    """AC-SR-20/22: the new dataset is importable and its catalog is keyed exactly."""
    from app.services.reports.datasets.delivery_order_lines import DATASET

    assert DATASET.key == "delivery_order_lines"
    dims = {c.key for c in DATASET.columns if c.tag == "dimension"}
    measures = {c.key for c in DATASET.columns if c.tag == "measure"}
    assert dims == {
        "customer", "product", "sales_agent", "location", "channel",
        "day", "week", "month", "delivery_order", "all",
    }, dims
    assert measures == {"amount", "qty"}, measures


def test_dataset_is_company_scoped_and_date_based_on_the_do_date():
    """AC-SR-20: company scope fail-closed (`scope == "company"` with a company column), date
    basis the DO's own `order_date`."""
    from app.models.order import Order
    from app.services.reports.datasets.delivery_order_lines import DATASET

    assert DATASET.scope == "company"
    assert DATASET.company_column is not None
    assert [b.key for b in DATASET.date_bases] == ["order_date"]
    assert str(DATASET.date_bases[0].expr) == str(Order.order_date)


def test_the_channel_rule_reads_demand_class_project_words():
    """Fix round 1, nit N1: the SQL channel rule is built from `demand_class.PROJECT_SEGMENTS`,
    the words `class_of` reads, never a second spelling of them."""
    from sqlalchemy.dialects import postgresql

    from app.services.reports.datasets.delivery_order_lines import CHANNEL_EXPR
    from app.services.scm.demand_class import PROJECT_SEGMENTS

    compiled = str(
        CHANNEL_EXPR.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).replace("%%", "%")  # the psycopg2 dialect escapes a literal % as %%
    for word in PROJECT_SEGMENTS:
        assert f"'%{word}%'" in compiled, (word, compiled)


# ============================================================ AC-SR-20: which DOs count


def test_cancelled_deleted_and_rep_dos_never_count(client, db):
    cust, prod, wh = _world(db)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), number="DO-GOOD",
            lines=[line(prod.id, wh.id, 5, price=D("10"), total=D("50.00"))])
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), number="DO-CANCELLED", is_cancelled=True,
            lines=[line(prod.id, wh.id, 100, price=D("10"), total=D("1000.00"))])
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), number="DO-DELETED", deleted=True,
            lines=[line(prod.id, wh.id, 200, price=D("10"), total=D("2000.00"))])
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), number="REP-0001",
            lines=[line(prod.id, wh.id, 300, price=D("10"), total=D("3000.00"))])
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), number="REPLACE-0002",
            lines=[line(prod.id, wh.id, 400, price=D("10"), total=D("4000.00"))])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="delivery_order")
    assert body["total"]["qty"] == 5, body
    assert money(body["total"]["amount"]) == D("50.00"), body
    assert [r["name"] for r in body["rows"]] == ["DO-GOOD"], body["rows"]


def test_another_customers_dos_are_never_counted(client, db):
    cust, prod, wh = _world(db)
    other = seed_customer(db, name=unique_code("Other"))
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
            lines=[line(prod.id, wh.id, 5, price=D("10"), total=D("50.00"))])
    seed_do(db, customer_id=other.id, order_date=date(2026, 8, 10),
            lines=[line(prod.id, wh.id, 500, price=D("10"), total=D("5000.00"))])
    db.commit()

    body = _get(client, customer_ids=cust.id)
    assert body["total"]["qty"] == 5, body
    assert money(body["total"]["amount"]) == D("50.00"), body


def test_company_scope_never_counts_another_companys_dos(client, db):
    """AC-SR-20: fail-closed company scope. A second company holds a product with the SAME
    code and DOs of its own; a Sorento-scoped caller sees only Sorento's."""
    mocha = seed_mocha(db)
    shared = unique_code("SKU")
    sorento_cust = seed_customer(db, name=unique_code("Cust"))
    mocha_cust = seed_customer(db, name=unique_code("MochaCust"), company_id=mocha.id)
    prod_a = product(db, company_id=DEFAULT_COMPANY_ID, code=shared)
    prod_b = product(db, company_id=mocha.id, code=shared)
    wh_a = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    wh_b = warehouse(db, company_id=mocha.id)
    seed_do(db, customer_id=sorento_cust.id, order_date=date(2026, 8, 10),
            lines=[line(prod_a.id, wh_a.id, 10, price=D("10"), total=D("100.00"))])
    seed_do(db, customer_id=mocha_cust.id, order_date=date(2026, 8, 10), company_id=mocha.id,
            lines=[line(prod_b.id, wh_b.id, 500, price=D("10"), total=D("5000.00"))])
    db.commit()

    body = _get(client, product_code=shared)
    assert body["total"]["qty"] == 10, body
    assert money(body["total"]["amount"]) == D("100.00"), body


# ============================================================ AC-SR-21: the amount


def test_legacy_do_counts_its_repeated_doc_total_once(client, db):
    """Real example: 11 lines each carrying the DOC total 9441.44. The DO is RM 9,441.44, not 11
    times that."""
    cust, _prod, wh = _world(db)
    prods = [product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code(f"P{i}")) for i in range(11)]
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), source_book=None,
            lines=[line(p.id, wh.id, 2, price=D("100"), total=D("9441.44")) for p in prods])
    db.commit()

    body = _get(client, customer_ids=cust.id)
    assert body["total"]["qty"] == 22, body
    assert money(body["total"]["amount"]) == D("9441.44"), body
    assert money(body["periods"][0]["amount"]) == D("9441.44"), body["periods"]


def test_legacy_do_product_shares_are_weighted_by_qty_price_and_discount(client, db):
    """Weights: A = 2 x 100 x (1 - 0) = 200, B = 1 x 100 x (1 - 0.5) = 50. DOC total 1000.00
    repeated on both lines -> A 800.00, B 200.00."""
    cust, _p, wh = _world(db)
    a = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SHARE-A")
    b = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SHARE-B")
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), source_book=None,
            lines=[
                line(a.id, wh.id, 2, price=D("100"), discount=D("0"), total=D("1000.00")),
                line(b.id, wh.id, 1, price=D("100"), discount=D("0.5"), total=D("1000.00")),
            ])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="product")
    by_name = {r["name"]: r for r in body["rows"]}
    assert money(by_name["ZZT-SHARE-A"]["amount"]) == D("800.00"), body["rows"]
    assert money(by_name["ZZT-SHARE-B"]["amount"]) == D("200.00"), body["rows"]
    assert by_name["ZZT-SHARE-A"]["qty"] == 2 and by_name["ZZT-SHARE-B"]["qty"] == 1, body["rows"]
    assert money(body["total"]["amount"]) == D("1000.00"), body


def test_a_discount_stored_as_a_percent_is_normalised(client, db):
    """Fix round 1, S2: a discount above 1 is a percent (real data carries 100.0000 on three
    lines). A legacy DO with a paid line and a 100%-off line: the free line is RM 0.00 and the
    paid line carries the whole DOC total."""
    cust, _p, wh = _world(db)
    paid = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-PCT-PAID")
    free = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-PCT-FREE")
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), source_book=None,
            lines=[
                line(paid.id, wh.id, 2, price=D("100"), discount=D("0"), total=D("200.00")),
                line(free.id, wh.id, 1, price=D("100"), discount=D("100"), total=D("200.00")),
            ])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="product")
    by_name = {r["name"]: money(r["amount"]) for r in body["rows"]}
    assert by_name == {"ZZT-PCT-PAID": D("200.00"), "ZZT-PCT-FREE": D("0.00")}, body["rows"]
    assert money(body["total"]["amount"]) == D("200.00"), body


def test_a_percent_discount_below_100_is_a_fraction(client, db):
    """37 stored is 37%: weights A = 1 x 100 x 0.63 = 63, B = 1 x 100 x 1 = 100 over a repeated
    163.00 split A 63.00, B 100.00."""
    cust, _p, wh = _world(db)
    a = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-PCT37-A")
    b = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-PCT37-B")
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), source_book=None,
            lines=[
                line(a.id, wh.id, 1, price=D("100"), discount=D("37"), total=D("163.00")),
                line(b.id, wh.id, 1, price=D("100"), discount=D("0"), total=D("163.00")),
            ])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="product")
    by_name = {r["name"]: money(r["amount"]) for r in body["rows"]}
    assert by_name == {"ZZT-PCT37-A": D("63.00"), "ZZT-PCT37-B": D("100.00")}, body["rows"]


def test_a_legacy_do_with_more_than_one_distinct_total_sums_its_own_line_math(client, db):
    """Fix round 1, S1 (owner ruling, option a) refined by fix round 2, rule a2: a legacy DO
    whose lines carry MORE THAN ONE distinct total is not one repeated DOC total, so nothing
    is split: each line is the LESSER of qty x unit price x (1 - discount), rounded to the sen,
    and the line's own total (a NULL total is the line math). Three real shapes, made-up codes:

    * 202606-3968: 2 x 370 (total 396.27) and 3 x 370 (total 594.41) -> 396.27 + 594.41 = 990.68
      (the stored totals are below the list-price math);

    * PS202607-0355: 2 x 523 (total 1046) and 3 x 523 (total 1569) -> 1,046 + 1,569 = 2,615.00;
    * M2609-0511: 3 x 320 d0 (960), 3 x 14 d1 (960), 2 x 320 d0 (640), 2 x 14 d1 (640)
      -> 960 + 0 + 640 + 0 = 1,600.00 (discount 1 = free).
    """
    cust, _p, wh = _world(db)
    ps_a = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-MIX-PS-A")
    ps_b = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-MIX-PS-B")
    seed_do(db, customer_id=cust.id, order_date=date(2026, 7, 10), number="ZZT-PS0355", source_book=None,
            lines=[
                line(ps_a.id, wh.id, 2, price=D("523"), total=D("1046.00")),
                line(ps_b.id, wh.id, 3, price=D("523"), total=D("1569.00")),
            ])
    m = [product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT-MIX-M-{i}") for i in range(4)]
    seed_do(db, customer_id=cust.id, order_date=date(2026, 9, 5), number="ZZT-M0511", source_book=None,
            lines=[
                line(m[0].id, wh.id, 3, price=D("320"), discount=D("0"), total=D("960.00")),
                line(m[1].id, wh.id, 3, price=D("14"), discount=D("1"), total=D("960.00")),
                line(m[2].id, wh.id, 2, price=D("320"), discount=D("0"), total=D("640.00")),
                line(m[3].id, wh.id, 2, price=D("14"), discount=D("1"), total=D("640.00")),
            ])
    r = [product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT-MIX-R-{i}") for i in range(2)]
    seed_do(db, customer_id=cust.id, order_date=date(2026, 6, 20), number="ZZT-R3968", source_book=None,
            lines=[
                line(r[0].id, wh.id, 2, price=D("370"), total=D("396.27")),
                line(r[1].id, wh.id, 3, price=D("370"), total=D("594.41")),
            ])
    db.commit()

    by_do = _get(client, customer_ids=cust.id, group_by="delivery_order")
    assert {r["name"]: money(r["amount"]) for r in by_do["rows"]} == {
        "ZZT-PS0355": D("2615.00"),
        "ZZT-M0511": D("1600.00"),
        "ZZT-R3968": D("990.68"),
    }, by_do["rows"]
    assert money(by_do["total"]["amount"]) == D("5205.68"), by_do

    by_product = _get(client, customer_ids=cust.id, group_by="product")
    assert {r["name"]: money(r["amount"]) for r in by_product["rows"]} == {
        "ZZT-MIX-PS-B": D("1569.00"),
        "ZZT-MIX-PS-A": D("1046.00"),
        "ZZT-MIX-M-0": D("960.00"),
        "ZZT-MIX-M-2": D("640.00"),
        "ZZT-MIX-M-1": D("0.00"),
        "ZZT-MIX-M-3": D("0.00"),
        "ZZT-MIX-R-1": D("594.41"),
        "ZZT-MIX-R-0": D("396.27"),
    }, by_product["rows"]


def test_a_product_filter_keeps_the_products_share_of_the_whole_do(client, db):
    """Fix round 1, S3: the share is computed over the WHOLE DO, then filtered. ZZT-SHARE-B is
    worth 200.00 of a repeated 1000.00 (A 800); asked for B alone it is still 200.00, never the
    whole 1000.00 re-split over the one line left."""
    cust, _p, wh = _world(db)
    a = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SHARE-A")
    b = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SHARE-B")
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), source_book=None,
            lines=[
                line(a.id, wh.id, 2, price=D("100"), discount=D("0"), total=D("1000.00")),
                line(b.id, wh.id, 1, price=D("100"), discount=D("0.5"), total=D("1000.00")),
            ])
    db.commit()

    body = _get(client, customer_ids=cust.id, product_code="ZZT-SHARE-B")
    assert money(body["total"]["amount"]) == D("200.00"), body
    assert body["total"]["qty"] == 1, body


def test_a_location_filter_keeps_that_warehouses_share_of_the_whole_do(client, db):
    """Fix round 1, S3: a legacy DO split across two warehouses (weights 300 and 100 over a
    repeated 400.00), filtered to one warehouse, answers that warehouse's share."""
    cust, prod, _w = _world(db)
    wh_a = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SPLIT-WA")
    wh_b = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SPLIT-WB")
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), source_book=None,
            lines=[
                line(prod.id, wh_a.id, 3, price=D("100"), total=D("400.00")),
                line(prod.id, wh_b.id, 1, price=D("100"), total=D("400.00")),
            ])
    db.commit()

    body = _get(client, customer_ids=cust.id, warehouse_codes="ZZT-SPLIT-WB")
    assert money(body["total"]["amount"]) == D("100.00"), body
    assert body["total"]["qty"] == 1, body


def test_equal_shares_when_every_weight_is_zero(client, db):
    """No unit price on any line: weights are all 0, so the DOC total splits equally."""
    cust, _p, wh = _world(db)
    prods = [product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT-EQ-{i}") for i in range(3)]
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), source_book=None,
            lines=[line(p.id, wh.id, 4, price=None, total=D("90.00")) for p in prods])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="product")
    assert [money(r["amount"]) for r in body["rows"]] == [D("30.00")] * 3, body["rows"]
    assert money(body["total"]["amount"]) == D("90.00"), body


def test_a_do_with_a_quantity_but_no_price_stays_in_at_zero(client, db):
    """The mock's DO-0824: a real qty, no price or total in the import. It stays in at RM 0.00."""
    cust, prod, wh = _world(db)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 28), number="DO-0824", source_book=None,
            lines=[line(prod.id, wh.id, 40, price=None, total=None)])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="delivery_order")
    assert body["total"]["qty"] == 40, body
    assert money(body["total"]["amount"]) == D("0.00"), body
    assert [(r["name"], r["qty"], money(r["amount"])) for r in body["rows"]] == [
        ("DO-0824", 40, D("0.00"))
    ], body["rows"]
    assert len(body["periods"]) == 1, "a priced-zero DO still counts as a delivery"


def test_autocount_do_sums_its_line_totals(client, db):
    """`source_book == "db1"`: each line carries its own total; the DO is their sum."""
    cust, _p, wh = _world(db)
    a = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-DB1-A")
    b = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-DB1-B")
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), source_book="db1",
            lines=[
                line(a.id, wh.id, 3, price=D("50"), total=D("150.00")),
                line(b.id, wh.id, 2, price=D("25"), total=D("50.00")),
            ])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="product")
    assert money(body["total"]["amount"]) == D("200.00"), body
    by_name = {r["name"]: money(r["amount"]) for r in body["rows"]}
    assert by_name == {"ZZT-DB1-A": D("150.00"), "ZZT-DB1-B": D("50.00")}, body["rows"]


def test_shares_tally_to_the_sen_when_a_total_does_not_split_evenly(client, db):
    """Three equal lines on a DOC total of 100.00: 33.33 x 3 would be 99.99. Every grouping must
    sum to the same Total EXACTLY, so the remaining sen is allocated, not dropped."""
    cust, _p, wh = _world(db)
    prods = [product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT-TALLY-{i}") for i in range(3)]
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), source_book=None,
            lines=[line(p.id, wh.id, 1, price=D("10"), total=D("100.00")) for p in prods])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="product")
    assert money(body["total"]["amount"]) == D("100.00"), body
    assert sum((money(r["amount"]) for r in body["rows"]), D("0")) == D("100.00"), body["rows"]


def test_every_grouping_sums_to_the_same_total(client, db):
    """Mixed legacy + AutoCount DOs over two accounts and two agents: customer, product,
    delivery order, sales agent and the periods all sum to the Total."""
    c1 = seed_customer(db, name="ZZT C1 SDN BHD")
    c2 = seed_customer(db, name="ZZT C2 SDN BHD")
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    p1 = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-G-P1")
    p2 = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-G-P2")
    p3 = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-G-P3")
    a1 = seed_agent(db, code="ZZT-AGENT-1")
    a2 = seed_agent(db, code="ZZT-AGENT-2")
    so1 = seed_so(db, customer_id=c1.id, agent_id=a1.id)
    so2 = seed_so(db, customer_id=c1.id, agent_id=a2.id)
    so3 = seed_so(db, customer_id=c2.id, agent_id=a2.id)
    # legacy: weights 200 and 50 over a repeated 1000.00 -> P1 800, P2 200
    seed_do(db, customer_id=c1.id, order_date=date(2026, 8, 10), number="DO-G1", source_book=None,
            sales_order_id=so1.id,
            lines=[
                line(p1.id, wh.id, 2, price=D("100"), total=D("1000.00")),
                line(p2.id, wh.id, 1, price=D("100"), discount=D("0.5"), total=D("1000.00")),
            ])
    # AutoCount: 150.00 + 50.00
    seed_do(db, customer_id=c1.id, order_date=date(2026, 8, 11), number="DO-G2", source_book="db1",
            sales_order_id=so2.id,
            lines=[
                line(p1.id, wh.id, 3, price=D("50"), total=D("150.00")),
                line(p3.id, wh.id, 2, price=D("25"), total=D("50.00")),
            ])
    # legacy, other account, September
    seed_do(db, customer_id=c2.id, order_date=date(2026, 9, 2), number="DO-G3", source_book=None,
            sales_order_id=so3.id,
            lines=[line(p2.id, wh.id, 4, price=D("10"), total=D("40.00"))])
    db.commit()

    window = {"customer_ids": f"{c1.id},{c2.id}", "date_from": "2026-08-01", "date_to": "2026-09-30"}
    base = _get(client, **window)
    assert base["total"]["qty"] == 12, base
    assert money(base["total"]["amount"]) == D("1240.00"), base
    assert base["grain"] == "month", base
    assert [(p["from"][:7], p["qty"], money(p["amount"])) for p in base["periods"]] == [
        ("2026-09", 4, D("40.00")),
        ("2026-08", 8, D("1200.00")),
    ], base["periods"]

    expected = {
        "product": {"ZZT-G-P1": D("950.00"), "ZZT-G-P2": D("240.00"), "ZZT-G-P3": D("50.00")},
        "customer": {"ZZT C1 SDN BHD": D("1200.00"), "ZZT C2 SDN BHD": D("40.00")},
        "delivery_order": {"DO-G1": D("1000.00"), "DO-G2": D("200.00"), "DO-G3": D("40.00")},
        "sales_agent": {"ZZT-AGENT-1": D("1000.00"), "ZZT-AGENT-2": D("240.00")},
    }
    for group_by, want in expected.items():
        body = _get(client, **window, group_by=group_by)
        got = {r["name"]: money(r["amount"]) for r in body["rows"]}
        assert got == want, (group_by, body["rows"])
        assert sum(got.values(), D("0")) == money(body["total"]["amount"]) == D("1240.00"), group_by
        assert sum(r["qty"] for r in body["rows"]) == body["total"]["qty"] == 12, group_by


# ============================================================ AC-SR-23: grain and periods


@pytest.mark.parametrize(
    "days,grain",
    [(1, "day"), (7, "day"), (8, "week"), (31, "week"), (32, "month")],
)
def test_grain_boundaries(client, db, days, grain):
    cust, prod, wh = _world(db)
    # a Monday; a one-day window is the DO's own day so it covers the seeded delivery
    start = date(2026, 8, 5) if days == 1 else date(2026, 8, 3)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 5),
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    end = start + timedelta(days=days - 1)
    body = _get(client, customer_ids=cust.id, date_from=start.isoformat(), date_to=end.isoformat())
    assert body["grain"] == grain, (days, body)
    assert len(body["periods"]) == 1, body["periods"]
    period = body["periods"][0]
    if grain == "day":
        assert period["from"] == period["to"] == "2026-08-05", period
    elif grain == "week":
        assert (period["from"], period["to"]) == ("2026-08-03", "2026-08-09"), period
    else:
        assert (period["from"], period["to"]) == ("2026-08-03", "2026-08-31"), period


def test_no_window_is_month_grain_over_the_whole_month(client, db):
    cust, prod, wh = _world(db)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 5),
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    body = _get(client, customer_ids=cust.id)
    assert body["grain"] == "month", body
    assert body["date_from"] is None and body["date_to"] is None, body
    assert [(p["from"], p["to"]) for p in body["periods"]] == [("2026-08-01", "2026-08-31")], body["periods"]


def test_week_periods_run_monday_to_sunday_clipped_to_the_window(client, db):
    """31 days from Sat 1 Aug 2026: the first week is Sat 1 to Sun 2, the last is Mon 31 alone.
    Only weeks with a delivery print, latest first."""
    cust, prod, wh = _world(db)
    for day, qty in ((1, 1), (5, 2), (12, 3), (31, 4)):
        seed_do(db, customer_id=cust.id, order_date=date(2026, 8, day),
                lines=[line(prod.id, wh.id, qty, price=D("10"), total=D(qty * 10))])
    db.commit()

    body = _get(client, customer_ids=cust.id, date_from="2026-08-01", date_to="2026-08-31")
    assert body["grain"] == "week", body
    assert [(p["from"], p["to"], p["qty"]) for p in body["periods"]] == [
        ("2026-08-31", "2026-08-31", 4),
        ("2026-08-10", "2026-08-16", 3),
        ("2026-08-03", "2026-08-09", 2),
        ("2026-08-01", "2026-08-02", 1),
    ], body["periods"]


def test_month_periods_clip_to_the_window_and_skip_empty_months(client, db):
    cust, prod, wh = _world(db)
    for d, qty in ((date(2026, 7, 20), 1), (date(2026, 9, 10), 2)):  # nothing in August
        seed_do(db, customer_id=cust.id, order_date=d,
                lines=[line(prod.id, wh.id, qty, price=D("10"), total=D(qty * 10))])
    seed_do(db, customer_id=cust.id, order_date=date(2026, 7, 10),  # before the window
            lines=[line(prod.id, wh.id, 100, price=D("10"), total=D("1000.00"))])
    db.commit()

    body = _get(client, customer_ids=cust.id, date_from="2026-07-15", date_to="2026-09-20")
    assert body["grain"] == "month", body
    assert [(p["from"], p["to"], p["qty"], money(p["amount"])) for p in body["periods"]] == [
        ("2026-09-01", "2026-09-20", 2, D("20.00")),
        ("2026-07-15", "2026-07-31", 1, D("10.00")),
    ], body["periods"]
    assert body["total"]["qty"] == 3, body


def test_day_periods_latest_first_and_only_days_with_a_delivery(client, db):
    cust, prod, wh = _world(db)
    for day, qty in ((3, 1), (5, 2)):
        seed_do(db, customer_id=cust.id, order_date=date(2026, 8, day),
                lines=[line(prod.id, wh.id, qty, price=D("10"), total=D(qty * 10))])
    db.commit()

    body = _get(client, customer_ids=cust.id, date_from="2026-08-03", date_to="2026-08-09")
    assert body["grain"] == "day", body
    assert [(p["from"], p["to"], p["qty"]) for p in body["periods"]] == [
        ("2026-08-05", "2026-08-05", 2),
        ("2026-08-03", "2026-08-03", 1),
    ], body["periods"]


def test_a_month_only_window_covers_the_whole_month(client, db):
    cust, prod, wh = _world(db)
    for day in (1, 31):
        seed_do(db, customer_id=cust.id, order_date=date(2026, 7, day),
                lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 1),
            lines=[line(prod.id, wh.id, 50, price=D("10"), total=D("500.00"))])
    db.commit()

    body = _get(client, customer_ids=cust.id, date_from="2026-07", date_to="2026-07")
    assert body["total"]["qty"] == 2, body
    assert body["grain"] == "week", "31 days is week grain"


# ============================================================ AC-SR-23: ranked rows


def test_group_by_product_is_ranked_by_amount_top_ten_with_the_rest_counted(client, db):
    cust, _p, wh = _world(db)
    prods = [product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT-RANK-{i:02d}") for i in range(12)]
    for i, p in enumerate(prods):
        # product i is worth (i + 1) x 10: the ranking is the REVERSE of insertion order
        seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), source_book="db1",
                lines=[line(p.id, wh.id, i + 1, price=D("10"), total=D((i + 1) * 10))])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="product")
    assert body["group_by"] == "product", body
    assert [r["rank"] for r in body["rows"]] == list(range(1, 11)), body["rows"]
    assert [r["name"] for r in body["rows"]] == [f"ZZT-RANK-{i:02d}" for i in range(11, 1, -1)], body["rows"]
    assert body["more"] == 2, body
    amounts = [money(r["amount"]) for r in body["rows"]]
    assert amounts == sorted(amounts, reverse=True), amounts
    # the total is over EVERY row, not just the ten printed
    assert body["total"]["qty"] == sum(range(1, 13)), body
    assert money(body["total"]["amount"]) == D(sum(range(1, 13)) * 10), body


def test_group_by_customer_ranks_accounts_by_amount(client, db):
    c1 = seed_customer(db, name="ZZT SMALL SDN BHD")
    c2 = seed_customer(db, name="ZZT BIG SDN BHD")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    seed_do(db, customer_id=c1.id, order_date=date(2026, 8, 10),
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    seed_do(db, customer_id=c2.id, order_date=date(2026, 8, 10),
            lines=[line(prod.id, wh.id, 9, price=D("10"), total=D("90.00"))])
    db.commit()

    body = _get(client, customer_ids=f"{c1.id},{c2.id}", group_by="customer")
    assert [(r["rank"], r["name"], r["qty"], money(r["amount"])) for r in body["rows"]] == [
        (1, "ZZT BIG SDN BHD", 9, D("90.00")),
        (2, "ZZT SMALL SDN BHD", 1, D("10.00")),
    ], body["rows"]
    assert body["more"] == 0, body


def test_group_by_delivery_order_is_date_desc_then_number_desc_and_carries_the_date(client, db):
    cust, prod, wh = _world(db)
    for number, day in (("DO-0815", 11), ("DO-0817", 27), ("DO-0823", 27), ("DO-0824", 28)):
        seed_do(db, customer_id=cust.id, order_date=date(2026, 8, day), number=number,
                lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="delivery_order")
    assert [(r["name"], r["date"]) for r in body["rows"]] == [
        ("DO-0824", "2026-08-28"),
        ("DO-0823", "2026-08-27"),
        ("DO-0817", "2026-08-27"),
        ("DO-0815", "2026-08-11"),
    ], body["rows"]
    assert [r["rank"] for r in body["rows"]] == [1, 2, 3, 4], body["rows"]


def test_delivery_order_rows_name_the_account_only_when_more_than_one_is_in_scope(client, db):
    c1 = seed_customer(db, name="ZZT ONE SDN BHD")
    c2 = seed_customer(db, name="ZZT TWO SDN BHD")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    seed_do(db, customer_id=c1.id, order_date=date(2026, 8, 10), number="DO-ONE",
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    seed_do(db, customer_id=c2.id, order_date=date(2026, 8, 11), number="DO-TWO",
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    single = _get(client, customer_ids=c1.id, group_by="delivery_order")
    assert single["customer_count"] == 1, single
    assert all(r.get("customer_name") is None for r in single["rows"]), single["rows"]

    multi = _get(client, customer_ids=f"{c1.id},{c2.id}", group_by="delivery_order")
    assert multi["customer_count"] == 2, multi
    assert {r["name"]: r["customer_name"] for r in multi["rows"]} == {
        "DO-ONE": "ZZT ONE SDN BHD",
        "DO-TWO": "ZZT TWO SDN BHD",
    }, multi["rows"]


def test_non_delivery_order_rows_carry_no_date(client, db):
    cust, prod, wh = _world(db)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="product")
    assert body["rows"] and all(r.get("date") is None for r in body["rows"]), body["rows"]


def test_without_group_by_there_are_no_rows(client, db):
    cust, prod, wh = _world(db)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    body = _get(client, customer_ids=cust.id)
    assert body["group_by"] is None, body
    assert body["rows"] == [] and body["more"] == 0, body


def test_group_by_sales_agent_is_the_top_agent_through_the_dos_sales_order(client, db):
    """A new perspective is a new dimension, not new code: the DO's SO's agent, ranked by amount."""
    cust, prod, wh = _world(db)
    big = seed_agent(db, code="ZZT-AGENT-BIG")
    small = seed_agent(db, code="ZZT-AGENT-SMALL")
    for agent, number, qty in ((big, "DO-AG1", 9), (big, "DO-AG2", 1), (small, "DO-AG3", 4)):
        so = seed_so(db, customer_id=cust.id, agent_id=agent.id)
        seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), number=number, sales_order_id=so.id,
                lines=[line(prod.id, wh.id, qty, price=D("10"), total=D(qty * 10))])
    db.commit()

    body = _get(client, customer_ids=cust.id, group_by="sales_agent")
    assert body["group_by"] == "sales_agent", body
    assert [(r["rank"], r["name"], r["qty"], money(r["amount"])) for r in body["rows"]] == [
        (1, "ZZT-AGENT-BIG", 10, D("100.00")),
        (2, "ZZT-AGENT-SMALL", 4, D("40.00")),
    ], body["rows"]


@pytest.mark.parametrize("bad", ["location", "month", "all", "so", "bogus"])
def test_an_unknown_group_by_is_422(client, db, bad):
    cust, _p, _w = _world(db)
    db.commit()
    resp = client.get(BASE, params={"customer_ids": cust.id, "group_by": bad})
    assert resp.status_code == 422, (bad, resp.text)
    assert "unknown_group_by" in resp.text, resp.text


@pytest.mark.parametrize("good", ["customer", "product", "delivery_order", "sales_agent"])
def test_every_documented_group_by_is_accepted_and_echoed(client, db, good):
    cust, prod, wh = _world(db)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()
    body = _get(client, customer_ids=cust.id, group_by=good)
    assert body["group_by"] == good, body


# ============================================================ AC-SR-24: options


def _option_keys(body):
    return [o["key"] for o in body["options"]]


def test_options_offer_all_three_for_several_accounts_and_products(client, db):
    c1 = seed_customer(db, name=unique_code("A"))
    c2 = seed_customer(db, name=unique_code("B"))
    p1 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P1"))
    p2 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P2"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    seed_do(db, customer_id=c1.id, order_date=date(2026, 8, 10),
            lines=[line(p1.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    seed_do(db, customer_id=c2.id, order_date=date(2026, 8, 10),
            lines=[line(p2.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    body = _get(client, customer_ids=f"{c1.id},{c2.id}")
    assert body["options"] == [
        {"key": "customer", "label": "By customer"},
        {"key": "product", "label": "By product"},
        {"key": "delivery_order", "label": "Delivery orders"},
    ], body["options"]


def test_options_leave_out_by_customer_for_one_account(client, db):
    cust, _p, wh = _world(db)
    p1 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P1"))
    p2 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P2"))
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
            lines=[line(p1.id, wh.id, 1, price=D("10"), total=D("10.00")),
                   line(p2.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    assert _option_keys(_get(client, customer_ids=cust.id)) == ["product", "delivery_order"]


def test_options_leave_out_by_product_when_only_one_product_delivered(client, db):
    c1 = seed_customer(db, name=unique_code("A"))
    c2 = seed_customer(db, name=unique_code("B"))
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ONLY"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    for c in (c1, c2):
        seed_do(db, customer_id=c.id, order_date=date(2026, 8, 10),
                lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    assert _option_keys(_get(client, customer_ids=f"{c1.id},{c2.id}")) == ["customer", "delivery_order"]


def test_the_product_count_is_the_products_delivered_in_scope_not_in_the_catalog(client, db):
    """A second product delivered on a cancelled DO, or outside the window, does not make
    By product worth offering."""
    cust, prod, wh = _world(db)
    other = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("OTHER"))
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), is_cancelled=True,
            lines=[line(other.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    seed_do(db, customer_id=cust.id, order_date=date(2026, 3, 10),
            lines=[line(other.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    body = _get(client, customer_ids=cust.id, date_from="2026-08-01", date_to="2026-08-31")
    assert _option_keys(body) == ["delivery_order"], body["options"]


@pytest.mark.parametrize("shown", ["customer", "product", "delivery_order"])
def test_options_never_offer_the_view_just_shown(client, db, shown):
    c1 = seed_customer(db, name=unique_code("A"))
    c2 = seed_customer(db, name=unique_code("B"))
    p1 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P1"))
    p2 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P2"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    seed_do(db, customer_id=c1.id, order_date=date(2026, 8, 10),
            lines=[line(p1.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    seed_do(db, customer_id=c2.id, order_date=date(2026, 8, 10),
            lines=[line(p2.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    keys = _option_keys(_get(client, customer_ids=f"{c1.id},{c2.id}", group_by=shown))
    assert shown not in keys, keys
    assert keys == [k for k in ("customer", "product", "delivery_order") if k != shown], keys


def test_a_miss_has_no_periods_rows_or_options(client, db):
    cust, prod, wh = _world(db)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 3, 10),
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    for group_by in (None, "product"):
        params = {"customer_ids": cust.id, "date_from": "2026-09-01", "date_to": "2026-09-30"}
        if group_by:
            params["group_by"] = group_by
        body = _get(client, **params)
        assert body["status"] == "ok", body
        assert body["periods"] == [] and body["rows"] == [] and body["options"] == [], body
        assert body["total"]["qty"] == 0 and money(body["total"]["amount"]) == D("0.00"), body


# ============================================================ AC-SR-25: contact policy


def _policy_world(db):
    """A scoped contact linked to one account; warehouses A and B, plus a REPAIR one."""
    cust = seed_customer(db, name="ZZT POLICY SDN BHD")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh_a = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-WH-A")
    wh_b = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-WH-B")
    repair = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-REPAIR")
    for qty, wh in ((10, wh_a), (20, wh_b), (40, repair)):
        seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
                lines=[line(prod.id, wh.id, qty, price=D("10"), total=D(qty * 10))])
    contact = _contact(db)
    _link(db, contact, cust)
    seed_policy(db, contact, warehouse_ids=[wh_a.id, wh_b.id])
    db.commit()
    return contact, cust, (wh_a, wh_b, repair)


def test_the_policy_caps_the_locations_when_none_is_named(client, db):
    """AC-SR-25: the contact's stock visibility policy caps locations, so a REPAIR line is not
    counted in an unqualified ask."""
    contact, _cust, _whs = _policy_world(db)
    body = _get(client, **_as_contact(contact))
    assert body["status"] == "ok", body
    assert body["total"]["qty"] == 30, body


def test_a_named_location_inside_the_policy_filters_to_it(client, db):
    contact, _cust, _whs = _policy_world(db)
    body = _get(client, warehouse_codes="ZZT-WH-A", location_token="ZZT-WH-A", **_as_contact(contact))
    assert body["status"] == "ok", body
    assert body["total"]["qty"] == 10, body
    assert body["location_token"] == "ZZT-WH-A" and body["warehouse_codes"] == ["ZZT-WH-A"], body


def test_a_named_location_outside_the_policy_is_refused_with_the_exact_line(client, db):
    contact, _cust, _whs = _policy_world(db)
    body = _get(client, warehouse_codes="ZZT-REPAIR", location_token="ZZT-REPAIR", **_as_contact(contact))
    assert body["status"] == "refused", body
    assert body["message"] == "Sorry, ZZT-REPAIR isn't one of the locations you can check.", body
    assert body["periods"] == [] and body["rows"] == [] and body["options"] == [], body


def test_the_refusal_names_the_location_as_typed_else_the_code(client, db):
    contact, _cust, _whs = _policy_world(db)
    typed = _get(client, warehouse_codes="ZZT-REPAIR", location_token="zzt-repair", **_as_contact(contact))
    assert typed["message"] == "Sorry, zzt-repair isn't one of the locations you can check.", typed
    bare = _get(client, warehouse_codes="ZZT-REPAIR", **_as_contact(contact))
    assert bare["status"] == "refused", bare
    assert bare["message"] == "Sorry, ZZT-REPAIR isn't one of the locations you can check.", bare


def test_an_excluded_location_is_outside_the_policy(client, db):
    """The policy's EXCLUDE list is the include list's sibling: naming a location on it is
    refused, and its lines are not counted in an unqualified ask."""
    cust = seed_customer(db, name=unique_code("Cust"))
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    ok = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-WH-OK")
    barred = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-WH-BARRED")
    for qty, wh in ((10, ok), (90, barred)):
        seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
                lines=[line(prod.id, wh.id, qty, price=D("10"), total=D(qty * 10))])
    contact = _contact(db)
    _link(db, contact, cust)
    seed_policy(db, contact, excluded_warehouse_ids=[barred.id])
    db.commit()

    assert _get(client, **_as_contact(contact))["total"]["qty"] == 10
    refused = _get(client, warehouse_codes="ZZT-WH-BARRED", location_token="ZZT-WH-BARRED", **_as_contact(contact))
    assert refused["status"] == "refused", refused


def test_an_empty_include_list_answers_ok_with_nothing(client, db):
    """Q4 (a): `warehouse_ids = []` means no location. Not an error and not a refusal: the
    header answer with no rows, and the engine is never handed `[]` as a filter (which would
    read as "no filter" and leak every warehouse)."""
    cust = seed_customer(db, name="ZZT EMPTY POLICY SDN BHD")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
            lines=[line(prod.id, wh.id, 77, price=D("10"), total=D("770.00"))])
    contact = _contact(db)
    _link(db, contact, cust)
    seed_policy(db, contact, warehouse_ids=[])
    db.commit()

    body = _get(client, **_as_contact(contact))
    assert body["status"] == "ok", body
    assert body["customer_name"] == "ZZT EMPTY POLICY SDN BHD", body
    assert body["total"]["qty"] == 0 and money(body["total"]["amount"]) == D("0.00"), body
    assert body["periods"] == [] and body["rows"] == [] and body["options"] == [], body


def test_without_a_contact_a_location_filter_is_just_a_filter(client, db):
    """No contact, no policy: `warehouse_codes` narrows lines, case-insensitively, and nothing is
    refused."""
    cust, prod, _w = _world(db)
    wh_a = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-NC-A")
    wh_b = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-NC-B")
    for qty, wh in ((10, wh_a), (20, wh_b)):
        seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
                lines=[line(prod.id, wh.id, qty, price=D("10"), total=D(qty * 10))])
    db.commit()

    body = _get(client, customer_ids=cust.id, warehouse_codes="zzt-nc-b")
    assert body["status"] == "ok", body
    assert body["total"]["qty"] == 20, body


# ============================================================ AC-SR-22 / AC-SR-26: channel


def _channel_world(db, *, segments):
    """One account per entry of `segments` (a segment code or None), one DO each worth 10 x n."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    ids, qtys = [], {}
    for i, seg in enumerate(segments, start=1):
        c = seed_customer(db, name=f"ZZT CH{i} SDN BHD", segment_code=seg)
        seed_do(db, customer_id=c.id, order_date=date(2026, 8, 10),
                lines=[line(prod.id, wh.id, i, price=D("10"), total=D(i * 10))])
        ids.append(c.id)
        qtys[seg or f"none{i}"] = i
    db.commit()
    return ",".join(ids)


def test_channel_is_shown_when_the_accounts_span_two_segment_classes(client, db):
    seed_segment(db, code="ZZT-RETAIL")
    seed_segment(db, code="ZZT-PROJECT-X")
    ids = _channel_world(db, segments=["ZZT-RETAIL", "ZZT-PROJECT-X"])
    body = _get(client, customer_ids=ids)
    assert body["channel_shown"] is True, body
    assert body["customer_count"] == 2, body


def test_channel_is_not_shown_for_one_segment_class(client, db):
    """Two accounts, both retail (different segment codes, same class): one class, no line."""
    seed_segment(db, code="ZZT-RETAIL-1")
    seed_segment(db, code="ZZT-RETAIL-2")
    ids = _channel_world(db, segments=["ZZT-RETAIL-1", "ZZT-RETAIL-2"])
    assert _get(client, customer_ids=ids)["channel_shown"] is False


def test_an_account_with_no_segment_adds_no_class(client, db):
    seed_segment(db, code="ZZT-RETAIL")
    ids = _channel_world(db, segments=["ZZT-RETAIL", None])
    assert _get(client, customer_ids=ids)["channel_shown"] is False


def test_a_project_filter_keeps_project_and_unsegmented_accounts_and_drops_retail(client, db):
    seed_segment(db, code="ZZT-RETAIL")
    seed_segment(db, code="ZZT-CONTRACT-Y")
    # qty 1 retail, qty 2 contract (= Project), qty 3 no segment
    ids = _channel_world(db, segments=["ZZT-RETAIL", "ZZT-CONTRACT-Y", None])
    body = _get(client, customer_ids=ids, channel="project")
    assert body["total"]["qty"] == 2 + 3, body
    assert body["channel_shown"] is True, body


def test_a_retail_filter_keeps_retail_and_unsegmented_accounts(client, db):
    seed_segment(db, code="ZZT-RETAIL")
    seed_segment(db, code="ZZT-PROJECT-X")
    ids = _channel_world(db, segments=["ZZT-RETAIL", "ZZT-PROJECT-X", None])
    body = _get(client, customer_ids=ids, channel="dealer")
    assert body["total"]["qty"] == 1 + 3, body


def test_a_channel_filter_the_accounts_do_not_span_is_ignored(client, db):
    """AC-SR-26: every account is retail, the ask says "project only": the filter is ignored
    (it would otherwise answer "No sales found" for an account that has plenty), and no Channel
    line is printed."""
    seed_segment(db, code="ZZT-RETAIL-1")
    seed_segment(db, code="ZZT-RETAIL-2")
    ids = _channel_world(db, segments=["ZZT-RETAIL-1", "ZZT-RETAIL-2"])
    body = _get(client, customer_ids=ids, channel="project")
    assert body["total"]["qty"] == 1 + 2, body
    assert body["channel_shown"] is False, body


# ============================================================ AC-SR-23: the body


def test_the_body_carries_every_declared_field_and_none_of_the_retired_ones(client, db):
    """`response_model` silently drops undeclared fields (LESSONS-LEARNT): assert them."""
    cust, prod, wh = _world(db)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    body = _get(client, customer_ids=cust.id, product_code=prod.product_code,
                location_token="ZZT-NOT-A-REAL-WAREHOUSE")
    for key in (
        "status", "message", "basis", "customer_name", "customer_count", "product_code",
        "product_codes", "channel", "channel_shown", "location_token", "warehouse_codes",
        "date_from", "date_to", "grain", "total", "periods", "group_by", "rows", "more", "options",
    ):
        assert key in body, f"missing field: {key}"
    assert body["status"] == "ok" and body["message"] is None, body
    assert body["basis"] == "delivered", body
    assert set(body["total"]) >= {"qty", "amount"}, body
    assert set(body["periods"][0]) >= {"from", "to", "qty", "amount"}, body["periods"]
    assert body["product_codes"] == [prod.product_code], body
    assert body["location_token"] == "ZZT-NOT-A-REAL-WAREHOUSE", "the token is echo only"
    for retired in ("months", "so_rows", "detail"):
        assert retired not in body, f"the retired {retired!r} key is still on the body"


def test_the_customer_echo_names_every_scoped_account_and_counts_them(client, db):
    c1 = seed_customer(db, name="ZZT Echo One")
    c2 = seed_customer(db, name="ZZT Echo Two")
    db.commit()
    body = _get(client, customer_ids=f"{c1.id},{c2.id}")
    assert body["customer_name"] == _customer_echo(db, None, [c1.id, c2.id]) == "ZZT Echo One, ZZT Echo Two", body
    assert body["customer_count"] == 2, body


def test_the_whole_report_is_aggregated_in_sql(client, db):
    """Forty DO lines, one SELECT over `order_lines` that groups in the database."""
    from sqlalchemy import event

    cust, prod, wh = _world(db)
    for i in range(40):
        seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10),
                lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    db.commit()

    connection = db.get_bind()
    calls: list[str] = []

    def _capture(conn, cursor, statement, *_a, **_kw):
        if statement.strip().upper().startswith("SELECT"):
            calls.append(statement)

    event.listen(connection, "before_cursor_execute", _capture)
    try:
        resp = client.get(BASE, params={"customer_ids": cust.id})
    finally:
        event.remove(connection, "before_cursor_execute", _capture)
    assert resp.status_code == 200, resp.text
    report_calls = [c for c in calls if "order_lines" in c.lower()]
    assert report_calls, "no SELECT touched order_lines at all"
    assert any("group by" in c.lower() for c in report_calls), report_calls


# ============================================================ fix round 1, security N1


def test_a_customer_scoped_contact_may_not_rank_sales_agents(client, db):
    """A linked (customer-scoped) contact asks about its own accounts; ranking the company's
    sales agents is staff information. 403 `group_by_not_allowed`."""
    cust, prod, wh = _world(db)
    agent = seed_agent(db, code="ZZT-AGENT-SCOPED")
    so = seed_so(db, customer_id=cust.id, agent_id=agent.id)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), sales_order_id=so.id,
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    contact = _contact(db)
    _link(db, contact, cust)
    db.commit()

    resp = client.get(BASE, params={"group_by": "sales_agent", **_as_contact(contact)})
    assert resp.status_code == 403, resp.text
    assert "group_by_not_allowed" in resp.text, resp.text
    # Every other drill stays open to it.
    assert _get(client, group_by="product", **_as_contact(contact))["group_by"] == "product"


def test_an_unlinked_contact_and_a_staff_caller_still_rank_sales_agents(client, db):
    cust, prod, wh = _world(db)
    agent = seed_agent(db, code="ZZT-AGENT-OPEN")
    so = seed_so(db, customer_id=cust.id, agent_id=agent.id)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 8, 10), sales_order_id=so.id,
            lines=[line(prod.id, wh.id, 1, price=D("10"), total=D("10.00"))])
    contact = _contact(db)  # no customer link: not customer-scoped
    db.commit()

    staff = _get(client, customer_ids=cust.id, group_by="sales_agent")
    assert [r["name"] for r in staff["rows"]] == ["ZZT-AGENT-OPEN"], staff["rows"]
    unlinked = _get(client, customer_ids=cust.id, group_by="sales_agent", **_as_contact(contact))
    assert [r["name"] for r in unlinked["rows"]] == ["ZZT-AGENT-OPEN"], unlinked["rows"]
