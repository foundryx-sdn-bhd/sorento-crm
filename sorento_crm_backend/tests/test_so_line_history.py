"""SO-DELIVERIES-VIEW: deliveries and Cancel SO documents under each SO line (AC-SD1..AC-SD11).

Plan: documentation/plans/scm/PLAN-so-deliveries-view-14oct.md. Postgres only, every row seeded
here (CI's database has none). The service tests run on the blank schema with NO company scope
(all companies visible), so "another company's DO is left out" proves the service filters on the
sales order's company itself rather than leaning on the session scope.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.models.autocount_cancel import AutocountCancelDoc, AutocountCancelLine
from app.models.base import set_company_scope
from app.models.company import Company
from app.models.inventory import Warehouse
from app.models.order import Order, OrderLine, OrderStatus, SalesOrder, SalesOrderLine
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import blank_session, unique_code
from tests.scm.conftest import scm_app  # noqa: F401

OTHER_COMPANY = "00000000-0000-0000-0000-0000000000a9"


def so_line_history(db, so):
    from app.services.scm.so_line_history import so_line_history as fn

    return fn(db, so)


def _u() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- seeds


def seed_product(db, company_id, code=None):
    cat = ProductCategory(id=_u(), category_code=unique_code("CAT")[:50], category_name="cat",
                          company_id=company_id)
    uom = UnitOfMeasure(id=_u(), uom_code=unique_code("U")[:20], uom_name="u", company_id=company_id)
    db.add_all([cat, uom])
    db.flush()
    p = Product(id=_u(), product_code=code or unique_code("SKU"), product_name="p",
                category_id=cat.id, base_uom_id=uom.id, list_price=0, is_active=True,
                company_id=company_id)
    db.add(p)
    db.flush()
    return p


def seed_wh(db, company_id, code=None):
    w = Warehouse(id=_u(), warehouse_code=code or unique_code("WH")[:40], warehouse_name="wh",
                  is_active=True, company_id=company_id)
    db.add(w)
    db.flush()
    return w


def seed_status(db, name):
    s = OrderStatus(id=_u(), status_code=unique_code("ST")[:50], status_name=name)
    db.add(s)
    db.flush()
    return s


def seed_so(db, company_id, so_number=None):
    so = SalesOrder(id=_u(), so_number=so_number or unique_code("SO"), status="open",
                    company_id=company_id)
    db.add(so)
    db.flush()
    return so


def seed_so_line(db, so, product, qty=10, company_id=DEFAULT_COMPANY_ID):
    line = SalesOrderLine(id=_u(), sales_order_id=so.id, product_id=product.id,
                          qty_ordered=qty, qty_delivered=0, line_status="open",
                          company_id=company_id)
    db.add(line)
    db.flush()
    return line


def seed_do(db, company_id, *, number=None, order_date=date(2026, 9, 1), cancelled=False,
            deleted=False, status=None, actual=None):
    o = Order(id=_u(), order_number=number or unique_code("DO"), order_date=order_date,
              is_cancelled=cancelled, company_id=company_id,
              order_status_id=status.id if status else None, actual_delivery_date=actual)
    if deleted:
        from datetime import datetime
        o.deleted_at = datetime(2026, 9, 20)
    db.add(o)
    db.flush()
    return o


def seed_do_line(db, order, product, wh, *, qty=5, from_doc_no=None, from_doc_type="SO",
                 so_line=None, seq=1):
    ol = OrderLine(id=_u(), order_id=order.id, product_id=product.id, warehouse_id=wh.id,
                   quantity=qty, line_sequence=seq, from_doc_no=from_doc_no,
                   from_doc_type=from_doc_type,
                   sales_order_line_id=so_line.id if so_line else None,
                   company_id=order.company_id)
    db.add(ol)
    db.flush()
    return ol


_dtl = [1000]


def seed_cancel(db, company_id, *, doc_no=None, doc_date=date(2026, 9, 5), kind="cancel_so",
                void=False, vanished=False, item_code=None, location="WH1", qty=3,
                from_doc_no=None, match_status="matched", match_reason="item",
                so_line=None, candidates=None, seq=1):
    from datetime import datetime, timezone
    _dtl[0] += 1
    doc = AutocountCancelDoc(
        id=_u(), kind=kind, source_book=unique_code("BK")[:20], doc_key=_dtl[0],
        doc_no=doc_no or unique_code("XS"), doc_date=doc_date, is_void=void,
        source_vanished_at=datetime(2026, 9, 6, tzinfo=timezone.utc) if vanished else None,
        company_id=company_id)
    db.add(doc)
    db.flush()
    line = AutocountCancelLine(
        id=_u(), cancel_doc_id=doc.id, dtl_key=_dtl[0], seq=seq, item_code=item_code,
        location_code=location, qty=qty, from_doc_no=from_doc_no, match_status=match_status,
        match_reason=match_reason, candidate_line_ids=candidates,
        sales_order_line_id=so_line.id if so_line else None, company_id=company_id)
    db.add(line)
    db.flush()
    return doc, line


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, None)
        s.add(Company(id=OTHER_COMPANY, name="Other Co", code=unique_code("OTH")[:20]))
        s.flush()
        yield s


def _world(db):
    """One SO with: product A on ONE line, product B on TWO lines."""
    so = seed_so(db, DEFAULT_COMPANY_ID)
    pa = seed_product(db, DEFAULT_COMPANY_ID, unique_code("WESERP"))
    pb = seed_product(db, DEFAULT_COMPANY_ID, unique_code("CFH"))
    wh1 = seed_wh(db, DEFAULT_COMPANY_ID)
    wh2 = seed_wh(db, DEFAULT_COMPANY_ID)
    la = seed_so_line(db, so, pa)
    lb1 = seed_so_line(db, so, pb)
    lb2 = seed_so_line(db, so, pb)
    db.refresh(so)
    return {"so": so, "pa": pa, "pb": pb, "wh1": wh1, "wh2": wh2, "la": la, "lb1": lb1, "lb2": lb2}


def _by_line(out, line):
    return next(r for r in out["lines"] if r["line_id"] == str(line.id))


def _group(out, sku):
    return next((g for g in out["groups"] if g["sku"] == sku), None)


# --------------------------------------------------------------------------- DO placement


def test_do_line_on_the_one_line_of_its_product_goes_under_it_even_from_another_warehouse(db):
    w = _world(db)
    d1 = seed_do(db, DEFAULT_COMPANY_ID, number="DO-ONE-A")
    d2 = seed_do(db, DEFAULT_COMPANY_ID, number="DO-ONE-B")
    seed_do_line(db, d1, w["pa"], w["wh1"], from_doc_no=w["so"].so_number, qty=4)
    seed_do_line(db, d2, w["pa"], w["wh2"], from_doc_no=w["so"].so_number, qty=6, from_doc_type=None)

    out = so_line_history(db, w["so"])

    rows = _by_line(out, w["la"])["deliveries"]
    assert [r["do_number"] for r in rows] == ["DO-ONE-A", "DO-ONE-B"]
    assert [r["qty"] for r in rows] == [4, 6]
    assert _group(out, w["pa"].product_code) is None


def test_do_line_with_a_stored_link_goes_under_that_line_even_when_the_product_is_on_two(db):
    w = _world(db)
    d = seed_do(db, DEFAULT_COMPANY_ID, number="DO-LINKED")
    seed_do_line(db, d, w["pb"], w["wh1"], so_line=w["lb2"], from_doc_no=None, from_doc_type=None, qty=7)

    out = so_line_history(db, w["so"])

    assert [r["do_number"] for r in _by_line(out, w["lb2"])["deliveries"]] == ["DO-LINKED"]
    assert _by_line(out, w["lb1"])["deliveries"] == []
    assert _group(out, w["pb"].product_code)["deliveries"] == []


def test_unlinked_do_line_of_a_product_on_two_lines_lands_in_the_group_under_neither_line(db):
    w = _world(db)
    d = seed_do(db, DEFAULT_COMPANY_ID, number="DO-GROUPED")
    seed_do_line(db, d, w["pb"], w["wh2"], from_doc_no=w["so"].so_number, qty=12)

    out = so_line_history(db, w["so"])

    g = _group(out, w["pb"].product_code)
    assert set(g["line_ids"]) == {str(w["lb1"].id), str(w["lb2"].id)}
    assert [r["do_number"] for r in g["deliveries"]] == ["DO-GROUPED"]
    assert _by_line(out, w["lb1"])["deliveries"] == []
    assert _by_line(out, w["lb2"])["deliveries"] == []


def test_do_rows_carry_their_fields_and_come_back_in_date_number_sequence_order(db):
    w = _world(db)
    st = seed_status(db, "Delivered")
    late = seed_do(db, DEFAULT_COMPANY_ID, number="DO-0002", order_date=date(2026, 9, 12),
                   status=st, actual=date(2026, 9, 14))
    early_b = seed_do(db, DEFAULT_COMPANY_ID, number="DO-0010", order_date=date(2026, 9, 3))
    early_a = seed_do(db, DEFAULT_COMPANY_ID, number="DO-0009", order_date=date(2026, 9, 3))
    seed_do_line(db, late, w["pa"], w["wh1"], from_doc_no=w["so"].so_number, qty=2)
    seed_do_line(db, early_b, w["pa"], w["wh1"], from_doc_no=w["so"].so_number, qty=3)
    seed_do_line(db, early_a, w["pa"], w["wh1"], from_doc_no=w["so"].so_number, qty=4)

    out = so_line_history(db, w["so"])

    rows = _by_line(out, w["la"])["deliveries"]
    assert [r["do_number"] for r in rows] == ["DO-0009", "DO-0010", "DO-0002"]
    last = rows[-1]
    assert set(last) == {"order_id", "do_number", "do_date", "location", "status",
                         "actual_delivery_date", "qty"}
    assert last["order_id"] == str(late.id)
    assert last["do_date"] == date(2026, 9, 12)
    assert last["location"] == w["wh1"].warehouse_code
    assert last["status"] == "Delivered"
    assert last["actual_delivery_date"] == date(2026, 9, 14)
    assert last["qty"] == 2
    assert rows[0]["actual_delivery_date"] is None
    assert rows[0]["status"] is None


def test_do_lines_that_do_not_belong_are_left_out(db):
    w = _world(db)
    so_no = w["so"].so_number
    ok = seed_do(db, DEFAULT_COMPANY_ID, number="DO-KEEP")
    seed_do_line(db, ok, w["pa"], w["wh1"], from_doc_no=so_no)
    voided = seed_do(db, DEFAULT_COMPANY_ID, number="DO-VOID", cancelled=True)
    seed_do_line(db, voided, w["pa"], w["wh1"], from_doc_no=so_no)
    deleted = seed_do(db, DEFAULT_COMPANY_ID, number="DO-DELETED", deleted=True)
    seed_do_line(db, deleted, w["pa"], w["wh1"], from_doc_no=so_no)
    stranger = seed_product(db, DEFAULT_COMPANY_ID)
    notonso = seed_do(db, DEFAULT_COMPANY_ID, number="DO-NOT-ON-SO")
    seed_do_line(db, notonso, stranger, w["wh1"], from_doc_no=so_no)
    other_so = seed_do(db, DEFAULT_COMPANY_ID, number="DO-OTHER-SO")
    seed_do_line(db, other_so, w["pa"], w["wh1"], from_doc_no=unique_code("SO"))
    # another company: same SO number, same product code
    # same product as the SO line, so only the company filter can drop it
    ocomp_wh = seed_wh(db, OTHER_COMPANY)
    foreign = seed_do(db, OTHER_COMPANY, number="DO-OTHER-CO")
    seed_do_line(db, foreign, w["pa"], ocomp_wh, from_doc_no=so_no)

    out = so_line_history(db, w["so"])

    everything = [r["do_number"] for line in out["lines"] for r in line["deliveries"]]
    everything += [r["do_number"] for g in out["groups"] for r in g["deliveries"]]
    assert everything == ["DO-KEEP"]


def test_a_do_line_of_another_document_type_is_not_a_delivery_of_this_so(db):
    w = _world(db)
    d = seed_do(db, DEFAULT_COMPANY_ID, number="DO-QT")
    seed_do_line(db, d, w["pa"], w["wh1"], from_doc_no=w["so"].so_number, from_doc_type="QT")

    out = so_line_history(db, w["so"])

    assert _by_line(out, w["la"])["deliveries"] == []


def test_do_line_of_this_company_under_a_do_header_of_another_company_is_left_out(db):
    w = _world(db)
    header = seed_do(db, OTHER_COMPANY, number="DO-FOREIGN-HEADER")
    seed_do_line(db, header, w["pa"], w["wh1"], from_doc_no=w["so"].so_number)
    header.company_id = OTHER_COMPANY
    db.query(OrderLine).filter(OrderLine.order_id == header.id).update(
        {OrderLine.company_id: DEFAULT_COMPANY_ID})
    db.flush()

    out = so_line_history(db, w["so"])

    assert _by_line(out, w["la"])["deliveries"] == []


def test_stale_link_to_this_so_line_is_ignored_when_from_doc_no_names_another_so(db):
    w = _world(db)
    d = seed_do(db, DEFAULT_COMPANY_ID, number="DO-STALE")
    seed_do_line(db, d, w["pa"], w["wh1"], so_line=w["la"], from_doc_no=unique_code("SO"))
    d2 = seed_do(db, DEFAULT_COMPANY_ID, number="DO-STALE-GRP")
    seed_do_line(db, d2, w["pb"], w["wh1"], so_line=w["lb1"], from_doc_no=unique_code("SO"))

    out = so_line_history(db, w["so"])

    assert _by_line(out, w["la"])["deliveries"] == []
    assert _by_line(out, w["lb1"])["deliveries"] == []
    assert _group(out, w["pb"].product_code)["deliveries"] == []


def test_the_same_link_with_no_from_doc_no_is_under_the_line(db):
    w = _world(db)
    d = seed_do(db, DEFAULT_COMPANY_ID, number="DO-LINK-NULL")
    seed_do_line(db, d, w["pa"], w["wh1"], so_line=w["la"], from_doc_no=None, from_doc_type=None)

    out = so_line_history(db, w["so"])

    assert [r["do_number"] for r in _by_line(out, w["la"])["deliveries"]] == ["DO-LINK-NULL"]


# --------------------------------------------------------------------------- Cancel SO


def test_item_matched_cancel_goes_under_its_line_with_qty_and_reason(db):
    w = _world(db)
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XS-0001", doc_date=date(2026, 9, 9),
                item_code=w["pa"].product_code, location="BRW", qty=36, so_line=w["la"],
                match_reason="item")

    out = so_line_history(db, w["so"])

    rows = _by_line(out, w["la"])["cancels"]
    assert rows == [{"doc_no": "XS-0001", "doc_date": date(2026, 9, 9), "location": "BRW",
                     "match_reason": "item", "qty": 36}]


def test_group_whole_cancel_goes_under_each_candidate_line_of_this_so_with_no_qty(db):
    w = _world(db)
    elsewhere = seed_so_line(db, seed_so(db, DEFAULT_COMPANY_ID), w["pb"])
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XS-WHOLE", item_code=w["pb"].product_code,
                match_reason="group_whole", match_status="matched", qty=84,
                candidates=[str(w["lb1"].id), str(w["lb2"].id), str(elsewhere.id)])

    out = so_line_history(db, w["so"])

    for line in (w["lb1"], w["lb2"]):
        rows = _by_line(out, line)["cancels"]
        assert [(r["doc_no"], r["match_reason"], r["qty"]) for r in rows] == [
            ("XS-WHOLE", "group_whole", None)]
    assert _by_line(out, w["la"])["cancels"] == []
    assert _group(out, w["pb"].product_code)["cancels"] == []


def test_undetermined_cancel_naming_this_so_goes_to_the_group_with_its_qty(db):
    w = _world(db)
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XS-UNDET", item_code=w["pb"].product_code,
                match_status="unmatched", match_reason="undetermined", qty=84,
                from_doc_no=w["so"].so_number)

    out = so_line_history(db, w["so"])

    g = _group(out, w["pb"].product_code)
    assert [(r["doc_no"], r["match_reason"], r["qty"]) for r in g["cancels"]] == [
        ("XS-UNDET", "undetermined", 84)]
    assert _by_line(out, w["lb1"])["cancels"] == []
    assert _by_line(out, w["lb2"])["cancels"] == []


def test_undetermined_cancel_for_a_one_line_product_or_another_so_is_left_out(db):
    w = _world(db)
    seed_cancel(db, DEFAULT_COMPANY_ID, item_code=w["pa"].product_code, match_status="unmatched",
                match_reason="undetermined", from_doc_no=w["so"].so_number)
    seed_cancel(db, DEFAULT_COMPANY_ID, item_code=w["pb"].product_code, match_status="unmatched",
                match_reason="undetermined", from_doc_no=unique_code("SO"))

    out = so_line_history(db, w["so"])

    assert _by_line(out, w["la"])["cancels"] == []
    assert _group(out, w["pb"].product_code)["cancels"] == []


def test_void_vanished_other_kind_waiting_not_on_order_and_other_company_cancels_are_left_out(db):
    w = _world(db)
    pcode = w["pa"].product_code
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XS-KEEP", item_code=pcode, so_line=w["la"])
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XS-VOID", item_code=pcode, so_line=w["la"], void=True)
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XS-GONE", item_code=pcode, so_line=w["la"], vanished=True)
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XP-PO", item_code=pcode, so_line=w["la"], kind="cancel_po")
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="CN-CONS", item_code=pcode, so_line=w["la"], kind="consignment")
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XS-WAIT", item_code=pcode, match_status="waiting",
                match_reason="order_not_found", from_doc_no=w["so"].so_number)
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XS-NOTON", item_code=pcode, match_status="unmatched",
                match_reason="item_not_on_order", from_doc_no=w["so"].so_number)
    seed_cancel(db, OTHER_COMPANY, doc_no="XS-OTHERCO", item_code=pcode, so_line=w["la"])

    out = so_line_history(db, w["so"])

    assert [r["doc_no"] for r in _by_line(out, w["la"])["cancels"]] == ["XS-KEEP"]


def test_cancels_are_ordered_by_date_then_document_number_then_seq(db):
    w = _world(db)
    p = w["pa"].product_code
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XS-3", doc_date=date(2026, 9, 9), item_code=p, so_line=w["la"])
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XS-2", doc_date=date(2026, 9, 2), item_code=p, so_line=w["la"])
    seed_cancel(db, DEFAULT_COMPANY_ID, doc_no="XS-1", doc_date=date(2026, 9, 2), item_code=p, so_line=w["la"])

    out = so_line_history(db, w["so"])

    assert [r["doc_no"] for r in _by_line(out, w["la"])["cancels"]] == ["XS-1", "XS-2", "XS-3"]


# --------------------------------------------------------------------------- shape


def test_empty_lines_have_empty_lists_and_a_two_line_product_has_its_group_while_a_one_line_product_has_none(db):
    w = _world(db)

    out = so_line_history(db, w["so"])

    assert {r["line_id"] for r in out["lines"]} == {str(w["la"].id), str(w["lb1"].id), str(w["lb2"].id)}
    for r in out["lines"]:
        assert r["deliveries"] == [] and r["cancels"] == []
    assert [g["sku"] for g in out["groups"]] == [w["pb"].product_code]
    g = out["groups"][0]
    assert set(g["line_ids"]) == {str(w["lb1"].id), str(w["lb2"].id)}
    assert g["deliveries"] == [] and g["cancels"] == []


# --------------------------------------------------------------------------- route (AC-SD11)


@pytest.fixture
def route_world(scm_app):
    app, db, gcu, gcuak = scm_app
    from tests.scm.conftest import SORENTO_COMPANY_ID, as_user, grant_permission, seed_user

    grant_permission(db, "purchasing", "scm.dashboard.view")
    as_user(app, gcu, gcuak, seed_user(db, "purchasing"))
    so = seed_so(db, SORENTO_COMPANY_ID)
    p = seed_product(db, SORENTO_COMPANY_ID, unique_code("WESERP"))
    wh = seed_wh(db, SORENTO_COMPANY_ID)
    line = seed_so_line(db, so, p, company_id=SORENTO_COMPANY_ID)
    st = seed_status(db, "Delivered")
    d = seed_do(db, SORENTO_COMPANY_ID, number="DO-ROUTE", order_date=date(2026, 9, 12),
                status=st, actual=date(2026, 9, 14))
    seed_do_line(db, d, p, wh, from_doc_no=so.so_number, qty=5)
    seed_cancel(db, SORENTO_COMPANY_ID, doc_no="XS-ROUTE", doc_date=date(2026, 9, 15),
                item_code=p.product_code, so_line=line, qty=2)
    return app, db, so, line, d, wh


def test_route_returns_the_history_with_iso_dates_for_a_permitted_user(route_world):
    app, db, so, line, d, wh = route_world

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{so.id}/deliveries")

    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"lines", "groups"}
    row = next(r for r in body["lines"] if r["line_id"] == str(line.id))
    assert row["deliveries"] == [{
        "order_id": str(d.id), "do_number": "DO-ROUTE", "do_date": "2026-09-12",
        "location": wh.warehouse_code, "status": "Delivered",
        "actual_delivery_date": "2026-09-14", "qty": 5.0}]
    assert row["cancels"] == [{"doc_no": "XS-ROUTE", "doc_date": "2026-09-15", "location": "WH1",
                               "match_reason": "item", "qty": 2.0}]
    assert body["groups"] == []


def test_route_is_forbidden_without_the_sales_order_read_permission(scm_app):
    app, db, gcu, gcuak = scm_app
    from tests.scm.conftest import SORENTO_COMPANY_ID, as_user, seed_user

    so = seed_so(db, SORENTO_COMPANY_ID)
    as_user(app, gcu, gcuak, seed_user(db, None))

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{so.id}/deliveries")

    assert res.status_code == 403, res.text


def test_route_answers_404_so_not_found_for_an_unknown_id(route_world):
    app, *_ = route_world

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{_u()}/deliveries")

    assert res.status_code == 404, res.text
    assert "SO_NOT_FOUND" in res.text


def test_route_answers_404_for_an_so_of_another_company(route_world):
    app, db, *_ = route_world
    db.add(Company(id=OTHER_COMPANY, name="Other Co", code=unique_code("OTH")[:20]))
    db.flush()
    foreign = seed_so(db, OTHER_COMPANY)

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{foreign.id}/deliveries")

    assert res.status_code == 404, res.text
    assert "SO_NOT_FOUND" in res.text


def test_route_answers_404_so_not_found_for_a_malformed_id(route_world):
    app, *_ = route_world

    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.get("/api/v1/scm/sales-orders/not-a-uuid/deliveries")

    assert res.status_code == 404, res.text
    assert "SO_NOT_FOUND" in res.text
