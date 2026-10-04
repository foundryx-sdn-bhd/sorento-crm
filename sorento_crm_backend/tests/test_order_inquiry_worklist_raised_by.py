"""Who pushed a sales order to purchasing, and when (PLAN section H, AC-H2 to AC-H4).

`order_inquiries.raised_by` / `raised_at` have been written at confirm since the feature
shipped and were shown nowhere: the worklist printed a raise DATE off the row and never
named the person, so "who asked for this" was a question only the database could answer.

Four things are pinned here, and each one is a way it would silently stop working:

* the row NAMES the person (`raised_by_name`), never their id - and it is asserted on the
  response JSON rather than on the service dict, because `response_model` drops a field
  the schema does not declare and the service would still look right;
* the one search box finds them, by name and by the front of their email address, which is
  what a person types when they only know "contact w";
* the `raised_by` filter narrows the list, and the summary offers ONLY users who have
  actually raised something (a picker listing every user in the company is a picker whose
  entries mostly return nothing);
* a RECONFIRM re-stamps the HEADER (AC-H4), while each ROW keeps naming the revision that
  actually raised it. Those are two different facts and the screen shows both: reading the
  re-stamped header per row would print the latest reconfirmer beside an older row's own
  clock ("B, 12/08 10:25" for a row A raised on 12 Aug).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.order import Customer, SalesOrder
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    DECISION_ACTIVE,
    DECISION_SUPERSEDED,
    INQUIRY_RAISED,
    IV_ORDER,
    OrderInquiry,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SOSupplyDecision,
)
from app.models.user import User

from ._pg_fixture import blank_session

MARKER = "zzt-oi-raised-by"
BASE = "/api/v1/project-sales"
LIST = f"{BASE}/order-inquiries"
SUMMARY = f"{BASE}/order-inquiries/summary"

READ_ONLY = ["projects.projects.view"]


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str, email: str) -> User:
    row = User(id=_uid(), email=email, name=name)
    db.add(row)
    db.flush()
    return row


def _product(db, code: str) -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:6]}", uom_name="Unit")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(),
        product_code=code,
        product_name=f"{MARKER} {code}",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal("100.00"),
    )
    db.add(row)
    db.flush()
    return row


def _adopted_order(db, company_id: str, so_number: str) -> ProjectSalesOrder:
    """An order out of the AutoCount book: no project registration at all, which is the
    shape almost every row on this worklist has."""
    customer = Customer(
        id=_uid(),
        company_id=company_id,
        customer_code=f"ZZT-{_uid()[:8]}",
        customer_name=f"{MARKER} Optad Sdn Bhd",
    )
    db.add(customer)
    db.flush()
    core = SalesOrder(
        id=_uid(),
        company_id=company_id,
        so_number=so_number,
        customer_id=customer.id,
        order_date=date(2026, 1, 8),
    )
    db.add(core)
    db.flush()
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=company_id,
        project_id=None,
        so_id=core.id,
        provisional_ref=so_number,
        autocount_doc_no=so_number,
        status="adopted",
    )
    db.add(order)
    db.flush()
    return order


def _line(db, company_id: str, order: ProjectSalesOrder, product: Product) -> ProjectSalesOrderLine:
    row = ProjectSalesOrderLine(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=order.id,
        line_no=1,
        product_id=product.id,
        description=f"{MARKER} line",
        qty=Decimal("932"),
        uom="UNIT",
        unit_price=Decimal("10.00"),
        amount=Decimal("9320.00"),
        delivery_date=date(2026, 3, 2),
    )
    db.add(row)
    db.flush()
    return row


def _inquiry(
    db, company_id: str, order: ProjectSalesOrder, *, raised_by: str, raised_at: datetime
) -> OrderInquiry:
    inquiry = OrderInquiry(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=order.id,
        state=INQUIRY_RAISED,
        raised_by=raised_by,
        raised_at=raised_at,
    )
    db.add(inquiry)
    db.flush()
    return inquiry


def _decision(
    db,
    company_id: str,
    order: ProjectSalesOrder,
    *,
    revision_no: int,
    confirmed_by: str,
    confirmed_at: datetime,
    supersedes: SOSupplyDecision | None = None,
) -> SOSupplyDecision:
    """One confirmed supply revision. The person on it is the person the rows it raised
    were raised BY - the header stamps the same person at the same moment."""
    if supersedes is not None:
        # One ACTIVE revision per order, enforced by a partial unique index - the same
        # rule the confirm path obeys, so the fixture has to obey it too.
        supersedes.state = DECISION_SUPERSEDED
        supersedes.superseded_at = confirmed_at
        db.flush()
    decision = SOSupplyDecision(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=order.id,
        revision_no=revision_no,
        state=DECISION_ACTIVE,
        line_snapshots=[],
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
    )
    db.add(decision)
    db.flush()
    return decision


def _row(
    db,
    company_id: str,
    inquiry: OrderInquiry,
    line: ProjectSalesOrderLine,
    item_code: str,
    *,
    decision: SOSupplyDecision | None = None,
):
    row = OrderInquiryRow(
        id=_uid(),
        company_id=company_id,
        order_inquiry_id=inquiry.id,
        so_line_id=line.id,
        item_code=item_code,
        qty=Decimal("932"),
        delivery_date=date(2026, 3, 2),
        verb=IV_ORDER,
        state=INQUIRY_RAISED,
        supply_decision_id=decision.id if decision else None,
    )
    db.add(row)
    db.flush()
    return row


def _client(db, user_id: str, permissions):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "user"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    granted = list(permissions)
    UserPermissionService.check_user_has_permission = (
        lambda self, uid, slug: slug in granted
    )
    UserPermissionService.get_user_permission_slugs = lambda self, uid: list(granted)
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


def _seed(db, company_id: str) -> dict:
    """Two CS users, one inquiry each, so every assertion below is about telling them
    apart rather than about the only row there is."""
    contact_w = _user(db, f"{MARKER} CONTACT W", f"contact w.{_uid()[:8]}@zzt.test")
    contact_aa = _user(db, f"{MARKER} CONTACT AA Tan", f"contact aa.{_uid()[:8]}@zzt.test")
    # A third CS user who has raised nothing: the filter's own list must not offer them.
    idle = _user(db, f"{MARKER} Never Raised", f"idle.{_uid()[:8]}@zzt.test")

    cindy_order = _adopted_order(db, company_id, f"ZZTSO{_uid()[:8]}")
    cindy_product = _product(db, f"ZZT-WESERP10B-{_uid()[:6]}")
    cindy_line = _line(db, company_id, cindy_order, cindy_product)
    cindy_inquiry = _inquiry(
        db,
        company_id,
        cindy_order,
        raised_by=contact_w.id,
        raised_at=datetime(2026, 8, 25, 0, 42),
    )
    cindy_decision = _decision(
        db,
        company_id,
        cindy_order,
        revision_no=1,
        confirmed_by=contact_w.id,
        confirmed_at=datetime(2026, 8, 25, 0, 42),
    )
    cindy_row = _row(
        db,
        company_id,
        cindy_inquiry,
        cindy_line,
        cindy_product.product_code,
        decision=cindy_decision,
    )

    contact_aa_order = _adopted_order(db, company_id, f"ZZTSO{_uid()[:8]}")
    contact_aa_product = _product(db, f"ZZT-M310CRPJ-{_uid()[:6]}")
    contact_aa_line = _line(db, company_id, contact_aa_order, contact_aa_product)
    contact_aa_inquiry = _inquiry(
        db,
        company_id,
        contact_aa_order,
        raised_by=contact_aa.id,
        raised_at=datetime(2026, 8, 22, 6, 10),
    )
    contact_aa_decision = _decision(
        db,
        company_id,
        contact_aa_order,
        revision_no=1,
        confirmed_by=contact_aa.id,
        confirmed_at=datetime(2026, 8, 22, 6, 10),
    )
    contact_aa_row = _row(
        db,
        company_id,
        contact_aa_inquiry,
        contact_aa_line,
        contact_aa_product.product_code,
        decision=contact_aa_decision,
    )

    db.commit()
    return {
        "contact w": contact_w,
        "contact aa": contact_aa,
        "idle": idle,
        "cindy_row": cindy_row,
        "contact_aa_row": contact_aa_row,
        "cindy_order": cindy_order,
        "cindy_line": cindy_line,
        "cindy_inquiry": cindy_inquiry,
        "cindy_decision": cindy_decision,
        "cindy_product": cindy_product,
    }


@pytest.fixture()
def api():
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        seeded = _seed(db, company_id)
        client, originals = _client(db, seeded["contact w"].id, READ_ONLY)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, db, company_id, seeded
        finally:
            _restore(originals)


# ------------------------------------------------------------------ AC-H2


def test_the_worklist_row_names_who_raised_it_and_when(api):
    client, _db, _company_id, seeded = api

    response = client.get(LIST)

    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()["data"]}
    contact_w = rows[seeded["cindy_row"].id]
    # The NAME, on the wire. A service that resolved it and a schema that dropped it
    # would still pass an assertion made against the service.
    assert contact_w["raised_by_name"] == seeded["contact w"].name
    assert contact_w["raised_at"] is not None
    # Never the id: the screen prints this cell as it comes.
    assert seeded["contact w"].id not in str(contact_w["raised_by_name"])


def test_a_row_whose_inquiry_names_nobody_reads_blank_rather_than_breaking(api):
    client, db, company_id, seeded = api

    order = _adopted_order(db, company_id, f"ZZTSO{_uid()[:8]}")
    product = _product(db, f"ZZT-ANON-{_uid()[:6]}")
    line = _line(db, company_id, order, product)
    inquiry = _inquiry(
        db, company_id, order, raised_by=None, raised_at=datetime(2026, 8, 20, 2, 0)
    )
    row = _row(db, company_id, inquiry, line, product.product_code)
    db.commit()

    response = client.get(LIST, params={"query": product.product_code})

    assert response.status_code == 200, response.text
    rows = {entry["id"]: entry for entry in response.json()["data"]}
    assert rows[row.id]["raised_by_name"] is None


def test_the_worklist_sorts_by_the_raising_person(api):
    client, _db, _company_id, seeded = api

    response = client.get(LIST, params={"sort": "raised_by_name", "dir": "asc"})

    assert response.status_code == 200, response.text
    names = [row["raised_by_name"] for row in response.json()["data"]]
    assert names == sorted(names, key=lambda value: (value is None, value or ""))


# ------------------------------------------------------------------ AC-H3


def test_searching_the_cs_users_name_returns_their_inquiries(api):
    client, _db, _company_id, seeded = api

    response = client.get(LIST, params={"query": "CONTACT W"})

    assert response.status_code == 200, response.text
    ids = {row["id"] for row in response.json()["data"]}
    assert seeded["cindy_row"].id in ids
    assert seeded["contact_aa_row"].id not in ids


def test_searching_the_front_of_the_cs_users_email_returns_their_inquiries(api):
    client, _db, _company_id, seeded = api
    prefix = seeded["contact aa"].email.split("@")[0][:10]

    response = client.get(LIST, params={"query": prefix})

    assert response.status_code == 200, response.text
    ids = {row["id"] for row in response.json()["data"]}
    assert seeded["contact_aa_row"].id in ids
    assert seeded["cindy_row"].id not in ids


def test_the_raised_by_filter_narrows_the_list_to_one_person(api):
    client, _db, _company_id, seeded = api

    response = client.get(LIST, params={"raised_by": seeded["contact w"].id})

    assert response.status_code == 200, response.text
    body = response.json()
    ids = {row["id"] for row in body["data"]}
    assert ids == {seeded["cindy_row"].id}
    assert body["pagination"]["total"] == 1


def test_the_summary_offers_only_people_who_have_raised_something(api):
    client, _db, _company_id, seeded = api

    response = client.get(SUMMARY)

    assert response.status_code == 200, response.text
    facets = response.json()["raised_by"]
    by_id = {entry["id"]: entry for entry in facets}
    assert by_id[seeded["contact w"].id]["label"] == seeded["contact w"].name
    assert by_id[seeded["contact w"].id]["rows"] == 1
    assert seeded["contact aa"].id in by_id
    # Somebody who has never raised an inquiry is not a filter option.
    assert seeded["idle"].id not in by_id


def test_the_raised_by_list_keeps_every_person_while_one_of_them_is_selected(api):
    client, _db, _company_id, seeded = api

    response = client.get(SUMMARY, params={"raised_by": seeded["contact w"].id})

    assert response.status_code == 200, response.text
    body = response.json()
    # The control drops its OWN filter, or picking a person makes every other person
    # disappear from the picker and it cannot be used a second time.
    assert {entry["id"] for entry in body["raised_by"]} >= {
        seeded["contact w"].id,
        seeded["contact aa"].id,
    }
    # The totals beside it still honour the filter, because they describe the screen.
    assert body["total_rows"] == 1


# ------------------------------------------------------------------ AC-H4


def test_a_reconfirm_by_somebody_else_leaves_the_earlier_rows_attributed_to_who_raised_them(api):
    """A confirms revision 1, B reconfirms as revision 2 covering another line.

    Three facts, and the defect is any two of them being conflated: the row A raised
    still reads A, the row B's revision raised reads B, and the HEADER reads B (AC-H4).
    Reading the header per row would repaint A's row as B's the moment B pressed confirm,
    while leaving A's clock on it.
    """
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    client, db, company_id, seeded = api
    order = seeded["cindy_order"]
    inquiry = seeded["cindy_inquiry"]
    first_line = seeded["cindy_line"]
    # A second line on the same order, so revision 2 has something of its own to raise.
    second_product = _product(db, f"ZZT-CWCSC605-{_uid()[:6]}")
    second_line = ProjectSalesOrderLine(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=order.id,
        line_no=2,
        product_id=second_product.id,
        description=f"{MARKER} line 2",
        qty=Decimal("400"),
        uom="UNIT",
        unit_price=Decimal("10.00"),
        amount=Decimal("4000.00"),
        delivery_date=date(2026, 3, 2),
    )
    db.add(second_line)
    db.flush()

    service = ProjectOrderInquiryService(db)
    # Revision 1 is the seeded one: A confirmed it, and it raised the row on line 1.
    first_raised_at = inquiry.raised_at
    assert seeded["cindy_row"].supply_decision_id == seeded["cindy_decision"].id
    assert first_line.id == seeded["cindy_line"].id

    revision_two = _decision(
        db,
        company_id,
        order,
        revision_no=2,
        confirmed_by=seeded["contact aa"].id,
        confirmed_at=datetime(2026, 8, 25, 2, 25),
        supersedes=seeded["cindy_decision"],
    )
    service.refresh_for_decision(
        order,
        revision_two,
        [
            {
                "line": second_line,
                "buy_qty": Decimal("400"),
                "item_code": second_product.product_code,
                "required_date": date(2026, 3, 2),
                "line_no": 2,
            }
        ],
        actor_user_id=seeded["contact aa"].id,
    )
    db.commit()

    # Owner ruling R5 (`PLAN-oi-header-list-detail.md`, 21 Sep 2026), superseding this
    # test's own former AC-H4 comment: the header's `raised_by`/`raised_at` are the
    # FIRST raise, fixed for life - a reconfirm never re-stamps them. B's reconfirm
    # instead adds its own `reconfirmed` row to the raise history (AC-RD-01), which is
    # where "who last confirmed this" is answered now.
    from app.models.project_so import OrderInquiryRaise

    db.refresh(inquiry)
    assert inquiry.raised_by == seeded["contact w"].id
    assert inquiry.raised_at == first_raised_at
    # This fixture's own header is built by a raw insert (`_inquiry` above), never
    # through `ensure_inquiry`, so it holds no `raised` row of its own - only what
    # `refresh_for_decision`'s reconfirm branch just added.
    raises = (
        db.query(OrderInquiryRaise)
        .filter(OrderInquiryRaise.order_inquiry_id == inquiry.id)
        .order_by(OrderInquiryRaise.raised_at.asc())
        .all()
    )
    assert [r.kind for r in raises] == ["reconfirmed"]
    assert raises[-1].raised_by == seeded["contact aa"].id

    response = client.get(LIST, params={"query": inquiry.inquiry_no})
    assert response.status_code == 200, response.text
    by_item = {row["item_code"]: row for row in response.json()["data"]}
    # B's revision raised B's row, and it is still open (`raised`) - visible by default.
    assert by_item[second_product.product_code]["raised_by_name"] == seeded["contact aa"].name

    # A's line was NOT named in revision 2, so _retire_uncovered_rows superseded A's own
    # row (a line the new revision does not cover, unrelated to this test's own point) -
    # a fact of this fixture, not of raised_by. R2 (`PLAN-oi-worklist-one-header.md`, 17
    # Sep) hides a cancelled row from the default list, so reading A's row back now
    # needs `state=cancelled`; its own raised_by still says A regardless of how many
    # times somebody else has confirmed since.
    cancelled_response = client.get(
        LIST, params={"query": inquiry.inquiry_no, "state": "cancelled"}
    )
    assert cancelled_response.status_code == 200, cancelled_response.text
    cancelled_by_item = {
        row["item_code"]: row for row in cancelled_response.json()["data"]
    }
    assert cancelled_by_item[seeded["cindy_product"].product_code]["raised_by_name"] == (
        seeded["contact w"].name
    )


def test_a_restated_unchanged_line_keeps_its_row_and_follows_the_new_confirmer(api):
    """AC-R2-10 (`PLAN-scm-oi-handover-r2-undo.md` S2, captain ruling 18 Sep) replaces
    the cancel-and-re-raise mechanic this test used to pin. Revision 2 NAMES A's own
    line again, at the SAME qty and date the row already carries (a plain `raised`
    ORDER row, no links, no redirect, same verb) - the widened settle-in-place gate
    now keeps that row AS IS: same row id, state stays `raised`, only its `supply_
    decision_id` moves to revision 2. Nothing is cancelled and nothing fresh is
    raised, so `raised_by` (`coalesce(decision.confirmed_by, row.acknowledged_by,
    inquiry.raised_by)`) now reads revision 2's OWN confirmer for this exact row -
    contact_aa, not contact_w - and contact_w's own name finds nothing at all, in any state:
    there is no historical, cancelled row left naming her the way there used to be.
    """
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    client, db, company_id, seeded = api
    order = seeded["cindy_order"]
    original_row_id = seeded["cindy_row"].id
    service = ProjectOrderInquiryService(db)
    revision_two = _decision(
        db,
        company_id,
        order,
        revision_no=2,
        confirmed_by=seeded["contact aa"].id,
        confirmed_at=datetime(2026, 8, 25, 2, 25),
        supersedes=seeded["cindy_decision"],
    )
    service.refresh_for_decision(
        order,
        revision_two,
        [
            {
                "line": seeded["cindy_line"],
                # The SAME qty and date the row already carries - AC-R2-10's own shape
                # (a restated UNCHANGED line). A changed need is a different AC
                # (AC-R2-11), pinned in test_order_inquiry_handover_automation.py, not
                # this file.
                "buy_qty": Decimal("932"),
                "item_code": seeded["cindy_product"].product_code,
                "required_date": date(2026, 3, 2),
                "line_no": 1,
            }
        ],
        actor_user_id=seeded["contact aa"].id,
    )
    db.commit()

    db.expire_all()
    row = db.query(OrderInquiryRow).filter(OrderInquiryRow.id == original_row_id).one()
    assert row.state == INQUIRY_RAISED, (
        "AC-R2-10: a restated unchanged line is settled in place, never cancelled"
    )
    assert row.supply_decision_id == revision_two.id, (
        "the row's own supply_decision_id must move to the new revision"
    )

    contact_aa_response = client.get(LIST, params={"raised_by": seeded["contact aa"].id})
    assert contact_aa_response.status_code == 200, contact_aa_response.text
    contact_aa_by_id = {r["id"]: r for r in contact_aa_response.json()["data"]}
    assert original_row_id in contact_aa_by_id, (
        "the row now follows revision 2's own confirmer, CONTACT AA"
    )
    assert contact_aa_by_id[original_row_id]["raised_by_name"] == seeded["contact aa"].name

    cindy_response = client.get(LIST, params={"raised_by": seeded["contact w"].id})
    assert cindy_response.status_code == 200, cindy_response.text
    assert cindy_response.json()["data"] == [], (
        "CONTACT W's own name must find nothing - the row she raised now follows CONTACT AA"
    )

    cindy_cancelled_response = client.get(
        LIST, params={"raised_by": seeded["contact w"].id, "state": "cancelled"}
    )
    assert cindy_cancelled_response.status_code == 200, cindy_cancelled_response.text
    assert cindy_cancelled_response.json()["data"] == [], (
        "AC-R2-10: nothing was cancelled, so there is no historical row left naming "
        "CONTACT W either"
    )


# ------------------------------------------- rows raised off an amendment, not a revision


def test_a_row_with_no_supply_revision_falls_back_to_its_own_headers_raiser(api):
    """An amendment raises its OWN inquiry and its rows carry no `supply_decision_id`
    (`ProjectOrderInquiryService._write`). That header's `raised_by` is never re-stamped -
    a reconfirm only touches the sales order's own inquiry - so it is the honest answer
    for those rows and the only one there is."""
    client, db, company_id, seeded = api

    order = _adopted_order(db, company_id, f"ZZTSO{_uid()[:8]}")
    product = _product(db, f"ZZT-AMEND-{_uid()[:6]}")
    line = _line(db, company_id, order, product)
    inquiry = _inquiry(
        db,
        company_id,
        order,
        raised_by=seeded["contact aa"].id,
        raised_at=datetime(2026, 8, 19, 9, 23),
    )
    row = _row(db, company_id, inquiry, line, product.product_code)  # no decision at all
    db.commit()

    response = client.get(LIST, params={"query": product.product_code})

    assert response.status_code == 200, response.text
    rows = {entry["id"]: entry for entry in response.json()["data"]}
    assert rows[row.id]["raised_by_name"] == seeded["contact aa"].name
