"""Slice 2 (`PLAN-oi-worklist-split-customer-project.md`, owner 18 Sep 2026): the Raised at
cell's own tooltip - purchasing sees the earlier raise of a carried line, not just the
latest re-confirm's own time.

Seeded DIRECTLY via ORM, not through `ProjectSupplyService.confirm`: this slice's own
read path (a correlated `json_agg` subquery in `order_inquiry_worklist_service.py`) does
not go anywhere near `ProjectSupplyService.confirm` at all, so there is nothing that call
would prove here that constructing the two-rows-one-line shape a re-confirm produces
(`project_order_inquiry_service._write`: the carried row is cancelled, a fresh one raised
under the same `order_inquiry_id`) by hand does not already - the same way
`tests/test_order_inquiry_worklist.py`'s own harness seeds every other shape without
going through the live confirm route either. Tests the worklist query in isolation.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models.project_so import (
    INQUIRY_CANCELLED,
    SO_STATUS_DRAFT,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from tests._pg_fixture import blank_session

from ..test_order_inquiry_worklist import (
    LIST,
    MARKER,
    READ_ONLY,
    _client,
    _inquiry_for,
    _product,
    _restore,
    _row,
    _sorento,
    _uid,
    _user,
    project_seed_service,
)


@pytest.fixture()
def raise_history_api():
    """One order_inquiry, one SO line, two rows on it - the CANCELLED predecessor
    (revision 1's own raise, earlier `created_at`) and the row that carries it today
    (revision 2). Plus one wholly unrelated fresh-raised row (its own line, its own
    inquiry, no predecessor) for the "nothing to show" case."""
    from app.models.base import company_scope
    from app.services.project_service import register_project

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        confirmer_id = _user(db, f"{MARKER} Farah")
        # K-GAP (review round 1): a SECOND, DIFFERENT user on the CURRENT row than on
        # its cancelled predecessor - a raiser-off-the-OUTER-row mutation (reading
        # `OrderInquiryRow.acknowledged_by` instead of the aliased HISTORICAL row's)
        # would then answer this second user's name instead of the first raiser's, and
        # only two distinct names in the fixture can tell the difference.
        second_confirmer_id = _user(db, f"{MARKER} Wong")
        project = register_project(
            db,
            company_id=company_id,
            actor_user_id=confirmer_id,
            developer_party_id=None,
            title=f"{MARKER} History Court",
        )

        # --- the carried line: two rows, same inquiry, same SO line.
        order = ProjectSalesOrder(
            id=_uid(),
            company_id=company_id,
            project_id=project.id,
            area_group="TOWER",
            provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
            autocount_doc_no="SO400111",
            status=SO_STATUS_DRAFT,
            grouping_origin="area",
            published_at=datetime(2025, 8, 1, 9, 0),
        )
        db.add(order)
        db.flush()
        product = _product(db, f"ZZT-HIST-{_uid()[:6]}", f"{MARKER} History basin")
        line = ProjectSalesOrderLine(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=order.id,
            line_no=1,
            product_id=product.id,
            description=f"{MARKER} history line",
            qty=Decimal("40"),
            uom="UNIT",
            unit_price=Decimal("10.00"),
            amount=Decimal("400.00"),
            delivery_date=date(2026, 5, 1),
        )
        db.add(line)
        db.flush()
        inquiry = _inquiry_for(db, company_id, order)
        old_row = _row(
            db,
            company_id,
            inquiry,
            so_line_id=line.id,
            item_code=product.product_code,
            qty="40",
            delivery_date=date(2026, 5, 1),
            state=INQUIRY_CANCELLED,
            note="Superseded by revision 2",
            acknowledged_by=confirmer_id,
            created_at=datetime(2026, 8, 1, 9, 15),
        )
        new_row = _row(
            db,
            company_id,
            inquiry,
            so_line_id=line.id,
            item_code=product.product_code,
            qty="40",
            delivery_date=date(2026, 5, 1),
            # A DIFFERENT raiser than `old_row`'s (K-GAP, review round 1) - see the note
            # on `second_confirmer_id` above.
            acknowledged_by=second_confirmer_id,
            created_at=datetime(2026, 8, 15, 10, 0),
        )
        # An earlier OPEN sibling on the SAME line, same inquiry - never cancelled
        # (blocker B1, review round 1): a second live instruction, not a superseded one.
        # On prod data an unfiltered `raise_history` marked 168 of these as "previously
        # raised" against one genuine supersede.
        open_sibling_row = _row(
            db,
            company_id,
            inquiry,
            so_line_id=line.id,
            item_code=product.product_code,
            qty="5",
            delivery_date=date(2026, 5, 1),
            created_at=datetime(2026, 8, 5, 9, 0),
        )

        # --- an unrelated, never-carried line: one row, nothing prior.
        fresh_order = ProjectSalesOrder(
            id=_uid(),
            company_id=company_id,
            project_id=project.id,
            area_group="TOWER",
            provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
            autocount_doc_no="SO400112",
            status=SO_STATUS_DRAFT,
            grouping_origin="area",
            published_at=datetime(2025, 8, 1, 9, 0),
        )
        db.add(fresh_order)
        db.flush()
        fresh_product = _product(db, f"ZZT-FRESH-{_uid()[:6]}", f"{MARKER} Fresh basin")
        fresh_line = ProjectSalesOrderLine(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=fresh_order.id,
            line_no=1,
            product_id=fresh_product.id,
            description=f"{MARKER} fresh line",
            qty=Decimal("15"),
            uom="UNIT",
            unit_price=Decimal("10.00"),
            amount=Decimal("150.00"),
            delivery_date=date(2026, 5, 2),
        )
        db.add(fresh_line)
        db.flush()
        fresh_inquiry = _inquiry_for(db, company_id, fresh_order)
        fresh_row = _row(
            db,
            company_id,
            fresh_inquiry,
            so_line_id=fresh_line.id,
            item_code=fresh_product.product_code,
            qty="15",
            delivery_date=date(2026, 5, 2),
            created_at=datetime(2026, 8, 15, 10, 0),
        )

        db.commit()
        client, originals = _client(db, confirmer_id, READ_ONLY)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, db, company_id, {
                    "old_row": old_row,
                    "new_row": new_row,
                    "open_sibling_row": open_sibling_row,
                    "fresh_row": fresh_row,
                    "confirmer_name": f"{MARKER} Farah",
                    "second_confirmer_name": f"{MARKER} Wong",
                }
        finally:
            _restore(originals)


def test_a_carried_rows_history_shows_the_earlier_raise(raise_history_api):
    client, _db, _company_id, seeded = raise_history_api

    body = client.get(LIST).json()
    by_id = {row["id"]: row for row in body["data"]}

    current = by_id[seeded["new_row"].id]
    # The row's OWN raiser is the second user (K-GAP, review round 1) - a distinct name
    # from the history entry below, so a mutation reading the raiser off the wrong row
    # cannot pass by accident.
    assert current["raised_by_name"] == seeded["second_confirmer_name"]
    assert current["raise_history"] == [
        {
            "raised_at": "2026-08-01T09:15:00",
            "raised_by_name": seeded["confirmer_name"],
        }
    ]
    # The cancelled predecessor is off the list itself (it is history, not an open
    # instruction) but still reachable directly, so it is worth confirming it carries no
    # tooltip of its own - nothing is prior to it.
    assert seeded["old_row"].id not in by_id


def test_an_open_sibling_row_on_the_same_line_is_not_history(raise_history_api):
    """Blocker B1 (review round 1): only a CANCELLED predecessor is history. An earlier
    OPEN row on the same line is a second LIVE instruction - the exact-list assertion in
    the test above already proves this (one entry, the cancelled one, not two), but this
    names the requirement on its own so a future edit cannot silently widen the filter
    again without a test failing under its own name."""
    client, _db, _company_id, seeded = raise_history_api

    body = client.get(LIST).json()
    by_id = {row["id"]: row for row in body["data"]}

    current = by_id[seeded["new_row"].id]
    raised_ats = [entry["raised_at"] for entry in current["raise_history"]]
    assert raised_ats == ["2026-08-01T09:15:00"]
    # The open sibling's own raise time never appears.
    assert "2026-08-05T09:00:00" not in raised_ats
    # It is still on the list itself - open, not history.
    assert seeded["open_sibling_row"].id in by_id


def test_a_fresh_row_carries_no_history(raise_history_api):
    client, _db, _company_id, seeded = raise_history_api

    body = client.get(LIST).json()
    by_id = {row["id"]: row for row in body["data"]}

    assert by_id[seeded["fresh_row"].id]["raise_history"] == []


def test_the_worklist_row_schema_declares_raise_history():
    """`response_model` silently drops an undeclared field."""
    from app.schemas.project_order_inquiry import (
        OrderInquiryRaiseHistoryEntry,
        OrderInquiryWorklistRow,
    )

    fields = OrderInquiryWorklistRow.model_fields
    assert "raise_history" in fields
    entry_fields = OrderInquiryRaiseHistoryEntry.model_fields
    assert "raised_at" in entry_fields
    assert "raised_by_name" in entry_fields


def test_the_export_carries_no_raise_history_column(raise_history_api):
    """Reversal (review round 4 Blocking 1): AC-LT-39/G9
    (`PLAN-oi-links-autocount-truth-24sep.md` 3.5) appends a SUGGESTED column after
    REMAINING - the cascade's own guess, beside PO and SPO on every other surface, while
    PO NO above stays real-links only. The header list this test pins gains it too."""
    client, _db, _company_id, _seeded = raise_history_api
    import io

    import openpyxl

    response = client.get(f"{LIST}/export")
    assert response.status_code == 200, response.text
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    sheet = book[book.sheetnames[0]]
    headings = [cell.value for cell in sheet[2] if cell.value]
    assert not any("history" in str(value).lower() for value in headings)
    assert headings == [
        "SO DATE",
        "S/O NO",
        "ITEM CODE",
        "QTY",
        "TOTAL QTY",
        "DELIVERY DATE",
        "PROJECT/CUSTOMER",
        "SUPPLIER",
        "PO NO ",
        "LOCATION",
        "ACKNOWLEDGED",
        # AC-D15 parity (22 Sep fix round): the row-level Taken/Remaining the grid has
        # shown since S3, appended after ACKNOWLEDGED.
        "TAKEN",
        "REMAINING",
        "SUGGESTED",
        # OI-LINE-SPLIT (UAC D2): appended last.
        "REMARK",
    ]
