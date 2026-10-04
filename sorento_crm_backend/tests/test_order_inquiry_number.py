"""An order inquiry has a NUMBER, not just an id.

`OI-2609-0001` (R3, `PLAN-oi-header-list-detail.md`): the MONTH of the header's own first
raise, Asia/Kuala_Lumpur, then a four-digit series that starts over every month - the
highest number this company has issued IN THAT MONTH, plus one. Superseded the flat
`OI-000001` (six digits, no month) this file used to pin; migration `523_oi_monthly_no_raises`
renumbers every pre-existing header and keeps the old value on `legacy_inquiry_no`. Until
this existed the only way to name what purchasing had been handed was a UUID, which nothing
in this product is allowed to show a person - and "the inquiry on SO414033" stops being an
answer the moment an amendment raises the second one.

The number is stamped by a `before_insert` listener on the model rather than by each writer,
so "no inquiry exists without a number" is structural: the two creation sites in the service
never mention it, and neither does any future one.

`tests/test_oi_monthly_number_and_raises.py` (S1 of that plan) is the primary suite for the
monthly scheme itself - the dated prefix, the MYT day-boundary edge, the never-re-minted
guard, the renumber migration. This file keeps only what that one does NOT already cover:
a gap in the series is never refilled (highest plus one, never the count), the database's
own uniqueness constraint, the confirmation flow's own minting path (`refresh_for_decision`,
not `ensure_inquiry`), and the detail read surviving the serialiser. Two tests that became
exact duplicates of the new file once rewritten to the dated format - "the first inquiry is
OI-000001" and "each inquiry takes the next number" (both just "N headers get consecutive
numbers", already covered by that file's AC-NO-01/AC-NO-02 tests) - are deleted rather than
kept as a second copy.

Postgres, blank scratch schema, rolled back at teardown, and every FK target real: the
uniqueness this slice leans on is a database constraint, so a test that ran anywhere else
would prove nothing about it.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.project_so import OrderInquiry, ProjectSalesOrder
from app.models.user import User
from app.services import project_order_inquiry_service as svc

from ._pg_fixture import blank_session

MARKER = "zzt-oino"

#: `OI-2609-0001`: prefix month + a four-digit series (`INQUIRY_NO_DIGITS`).
DATED_NUMBER_RE = re.compile(r"^OI-\d{4}-\d{4}$")


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


def _company(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _order(db, company_id: str) -> ProjectSalesOrder:
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=company_id,
        project_id=None,
        provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
        status="draft",
    )
    db.add(order)
    db.flush()
    return order


def _inquiry(db, order: ProjectSalesOrder, *, amendment_id=None, raised_at=None) -> OrderInquiry:
    row = OrderInquiry(
        id=_uid(),
        company_id=order.company_id,
        project_sales_order_id=order.id,
        amendment_id=amendment_id,
        state="raised",
    )
    if raised_at is not None:
        row.raised_at = raised_at
    db.add(row)
    db.flush()
    return row


def test_a_gap_in_the_series_is_never_refilled(db):
    """The next number is the HIGHEST plus one, never the count, scoped to the MONTH the
    header opened under (R3).

    Counting would hand a departed inquiry's number to a different one the moment anything
    was removed, and the old number is already in somebody's email. (Removing the highest
    number of all does free it again - the same property `PSO-000001` has, and the same
    non-event: nothing in this system deletes an inquiry, rows are cancelled.)
    """
    company_id = _company(db)
    september = datetime(2026, 9, 5)
    dropped = _inquiry(db, _order(db, company_id), raised_at=september)
    kept = _inquiry(db, _order(db, company_id), raised_at=september)
    assert (dropped.inquiry_no, kept.inquiry_no) == ("OI-2609-0001", "OI-2609-0002")
    db.delete(dropped)
    db.flush()

    assert svc.next_inquiry_no(db, company_id, september.date()) == "OI-2609-0003"


def test_the_number_is_unique_in_the_database(db):
    """Enforced by the table, not by remembering to check: a document number two records
    share is worth less than no number at all."""
    company_id = _company(db)
    first = _inquiry(db, _order(db, company_id))
    order = _order(db, company_id)

    db.add(OrderInquiry(
        id=_uid(),
        company_id=order.company_id,
        project_sales_order_id=order.id,
        state="raised",
        inquiry_no=first.inquiry_no,
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_a_confirmation_stamps_the_number_on_the_inquiry_it_raises(db):
    """The confirmed Buy handoff is one of the two places an inquiry is born, and it says
    nothing about numbering: the stamp does that, which is the point of putting it there.

    Exercises `refresh_for_decision`'s own minting path end to end - a different route
    from `tests/test_oi_monthly_number_and_raises.py`'s direct `ensure_inquiry`/model-insert
    calls, so it stays even though that file owns the numbering SCHEME itself. No
    `raised_at` is set on the way in here, so the header opens under WHATEVER real month
    the test happens to run in - asserted by shape (`DATED_NUMBER_RE`) and by the `-0001`
    a brand-new company-scoped scratch schema guarantees for that month, never by a
    hardcoded month.
    """
    from app.models.product import Product, ProductCategory, UnitOfMeasure
    from app.models.project_so import ProjectSalesOrderLine, SOSupplyDecision

    company_id = _company(db)
    order = _order(db, company_id)
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:6]}", uom_name="Unit")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat")
    db.add_all([uom, category])
    db.flush()
    product = Product(
        id=_uid(), product_code=f"ZZT-{_uid()[:8]}", product_name=f"{MARKER} item",
        category_id=category.id, base_uom_id=uom.id, list_price=Decimal("10.00"))
    db.add(product)
    db.flush()
    line = ProjectSalesOrderLine(
        id=_uid(), company_id=company_id, project_sales_order_id=order.id, line_no=1,
        product_id=product.id, description=f"{MARKER} line", qty=Decimal("5"), uom="UNIT",
        unit_price=Decimal("10.00"), amount=Decimal("50.00"),
        delivery_date=date(2026, 9, 1))
    db.add(line)
    db.flush()
    actor = User(id=_uid(), email=f"{_uid()}@zzt.test", name=f"{MARKER} CONTACT AH")
    db.add(actor)
    db.flush()
    decision = SOSupplyDecision(
        id=_uid(), company_id=company_id, project_sales_order_id=order.id, revision_no=1,
        state="active", line_snapshots=[{"line_no": 1}], confirmed_by=actor.id,
        confirmed_at=datetime.utcnow())
    db.add(decision)
    db.flush()

    result = svc.ProjectOrderInquiryService(db).refresh_for_decision(
        order, decision,
        [{"line": line, "line_no": 1, "item_code": product.product_code,
          "buy_qty": Decimal("5"), "required_date": line.delivery_date,
          "stock_location": None}],
        actor_user_id=actor.id,
    )

    inquiry_no = result["inquiry"].inquiry_no
    assert DATED_NUMBER_RE.match(inquiry_no), inquiry_no
    assert inquiry_no.endswith("-0001"), (
        "the FIRST inquiry raised in a fresh, company-scoped scratch schema this month"
    )


def test_the_detail_read_names_the_inquiry(db):
    """It is what the screen prints, so it has to survive the serialiser as well as the
    column - the same lesson `response_model` teaches every time it drops a field."""
    company_id = _company(db)
    order = _order(db, company_id)
    inquiry = _inquiry(db, order)

    detail = svc.ProjectOrderInquiryService(db).get_for_sales_order(order.id)

    assert detail is not None
    assert detail["inquiry_no"] == inquiry.inquiry_no
