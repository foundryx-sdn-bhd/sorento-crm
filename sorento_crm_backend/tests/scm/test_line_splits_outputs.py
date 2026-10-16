"""OI-LINE-SPLIT slice 2: what purchasing gets (UAC D1, D2, D3).

Splits are saved through the real PUT route, then read off the three outputs: the OI Excel
(`OrderInquiryWorklistService.export_xlsx`), the handover email lines, and the order
inquiries Lines tab serializer. Every row is seeded here.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal

import openpyxl
import pytest

from app.services.order_inquiry_worklist_service import (
    EXPORT_HEADINGS,
    OrderInquiryWorklistService,
)
from app.services.project_order_inquiry_service import (
    ProjectOrderInquiryService,
    _handover_fmt_date,
)
from tests.scm.test_line_splits_api import _put, _sp, _url  # noqa: F401
from tests.test_planning_change_apply_on_board import (  # noqa: F401 - fixtures reused
    _confirm,
    _order_row,
    api,
    world,
)
from tests.test_planning_changes import (
    _core_line,
    _core_so,
    _line_payload,
    _project_line,
    _project_so,
)

TRIGGER = "order_inquiry_handover"
D_A = date(2026, 11, 2)
D_B = date(2026, 11, 9)


class _S:
    pass


def _order(api, lines):
    """One published order whose lines (line_no, qty, date) are all confirmed as Buy."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    s = _S()
    s.client, s.world, s.db, s.order, s.core_so, s.line = client, world, db, order, core_so, {}
    for no, qty, when in lines:
        core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=str(qty),
                          required_date=when)
        s.line[no] = _project_line(db, order, line_no=no, product=world.product, core_line=core)
        s.line[no].stock_location = world.own_wh.warehouse_code
    db.commit()
    return s


def _confirm_all(s):
    payload = [_line_payload(l.id, buy_qty=str(l.qty)) for l in s.line.values()]
    r = _confirm(s.client, s.order.id, payload)
    assert r.status_code == 200, r.text
    s.db.commit()


def _save(s, by_line_no):
    body = [{"line_id": str(s.line[no].id), "splits": splits}
            for no, splits in by_line_no.items()]
    r = _put(s, body)
    assert r.status_code == 200, f"premise: the splits save ({r.status_code}) {r.text}"


def _on(s, no):
    return str(s.line[no].id)


def _sheet_rows(s):
    _name, data = OrderInquiryWorklistService(s.db).export_xlsx(query=s.core_so.so_number)
    book = openpyxl.load_workbook(io.BytesIO(data))
    assert len(book.sheetnames) == 1, book.sheetnames
    rows = list(book.worksheets[0].iter_rows(values_only=True))
    heads = list(rows[1])
    return heads, [r for r in rows[2:] if r[1]]


def _day(value):
    return value.date() if isinstance(value, datetime) else value


# ---------------------------------------------------------------------------
# (a) OI Excel
# ---------------------------------------------------------------------------


def test_remark_is_the_last_heading_and_the_ten_before_it_are_unchanged():
    assert EXPORT_HEADINGS[-1] == "REMARK"
    assert EXPORT_HEADINGS[:10] == (
        "SO DATE", "S/O NO", "ITEM CODE", "QTY", "TOTAL QTY", "DELIVERY DATE",
        "PROJECT/CUSTOMER", "SUPPLIER", "PO NO ", "LOCATION",
    )
    assert EXPORT_HEADINGS[10:14] == ("ACKNOWLEDGED", "TAKEN", "REMAINING", "SUGGESTED")


def test_unsplit_rows_print_once_with_an_empty_remark(api):
    s = _order(api, [(10, 10, D_A), (20, 6, D_B)])
    _confirm_all(s)
    heads, rows = _sheet_rows(s)
    assert heads[-1] == "REMARK"
    col = heads.index("REMARK")
    assert [r[3] for r in rows] == [10.0, 6.0] or sorted(r[3] for r in rows) == [6.0, 10.0]
    assert all(not r[col] for r in rows), [r[col] for r in rows]


def test_a_row_whose_splits_add_up_prints_one_row_per_split(api):
    s = _order(api, [(10, 10, D_A)])
    _confirm_all(s)
    _save(s, {10: [_sp(4, D_A, on=_on(s, 10)), _sp(6, D_B, on=_on(s, 10), remark="site not ready")]})
    heads, rows = _sheet_rows(s)
    col = heads.index("REMARK")
    assert len(rows) == 2, rows
    by_qty = {r[3]: r for r in rows}
    assert set(by_qty) == {4.0, 6.0}
    assert _day(by_qty[4.0][5]) == D_A and _day(by_qty[6.0][5]) == D_B
    assert by_qty[4.0][col].startswith("REMAIN")
    assert by_qty[6.0][col].startswith("DELAY") and "site not ready" in by_qty[6.0][col]
    # TOTAL QTY stays the sum of the printed rows, on the last row of the run
    assert rows[-1][4] == 10.0


def test_splits_swapped_between_two_lines_print_from_line_n(api):
    s = _order(api, [(40, 10, D_A), (50, 12, D_A)])
    _confirm_all(s)
    _save(s, {
        40: [_sp(6, D_A, on=_on(s, 40)), _sp(4, D_B, on=_on(s, 50))],
        50: [_sp(8, D_A, on=_on(s, 50)), _sp(4, D_B, on=_on(s, 40))],
    })
    heads, rows = _sheet_rows(s)
    col = heads.index("REMARK")
    assert len(rows) == 4, [(r[3], r[col]) for r in rows]
    assert sorted(r[3] for r in rows) == [4.0, 4.0, 6.0, 8.0]
    remarks = [r[col] for r in rows]
    assert any("from line 40" in x for x in remarks), remarks
    assert any("from line 50" in x for x in remarks), remarks
    assert rows[-1][4] == 22.0, "TOTAL QTY is the sum of the rows"


def test_a_row_whose_splits_do_not_add_up_prints_once_with_the_plan_in_remark(api):
    s = _order(api, [(20, 8, D_A), (30, 6, D_A)])
    _confirm_all(s)
    # line 20 keeps 5 and parks 3 on line 30: on line 30 the splits sitting there are 3, not 6
    _save(s, {20: [_sp(5, D_A, on=_on(s, 20)), _sp(3, D_B, on=_on(s, 30))]})
    heads, rows = _sheet_rows(s)
    col = heads.index("REMARK")
    assert len(rows) == 2, rows
    assert sorted(r[3] for r in rows) == [6.0, 8.0]
    by_qty = {r[3]: r for r in rows}
    assert by_qty[8.0][col] and "5" in by_qty[8.0][col] and "3" in by_qty[8.0][col]
    assert by_qty[6.0][col] and "3" in by_qty[6.0][col]


# ---------------------------------------------------------------------------
# (b) handover email lines
# ---------------------------------------------------------------------------


@pytest.fixture()
def emails(monkeypatch):
    from app.services.project_order_inquiry_service import (
        register_order_inquiry_post_commit_dispatch,
    )

    register_order_inquiry_post_commit_dispatch()
    sent: list = []

    def _fake_dispatch(self, trigger_type, *, context, source_kind, source_id):
        if trigger_type == TRIGGER:
            sent.append(context["handover"])
        return {"trigger_type": trigger_type, "fired": 0, "results": []}

    monkeypatch.setattr(
        "app.services.automation_service.AutomationService.dispatch_event", _fake_dispatch
    )
    return sent


def test_a_split_line_is_one_email_line_per_split_each_with_its_was(api, emails):
    s = _order(api, [(10, 10, D_A)])
    _save(s, {10: [
        _sp(4, D_A, on=_on(s, 10)),
        _sp(4, D_B, on=_on(s, 10), remark="after site visit"),
        _sp(2, None, on=_on(s, 10), cancel=True),
    ]})
    _confirm_all(s)
    assert len(emails) == 1, emails
    lines = emails[0]["lines"]
    assert len(lines) == 3, [(l["qty"], l["remark"]) for l in lines]
    base_date = _handover_fmt_date(D_A)
    for line in lines:
        assert line["item_code"] == s.world.product.product_code
        assert line["location"] == s.world.own_wh.warehouse_code
        assert line["was"] == {"qty": "10", "delivery_date": base_date}, line
    by_word = {l["remark"].split(",")[0].split(" ")[0]: l for l in lines}
    assert set(by_word) >= {"REMAIN", "DELAY", "CANCEL"}, [l["remark"] for l in lines]
    assert by_word["REMAIN"]["qty"] == "4" and by_word["REMAIN"]["delivery_date"] == base_date
    assert by_word["DELAY"]["qty"] == "4"
    assert by_word["DELAY"]["delivery_date"] == _handover_fmt_date(D_B)
    assert "after site visit" in by_word["DELAY"]["remark"]
    assert by_word["CANCEL"]["qty"] == "2"
    assert by_word["CANCEL"]["remark"].startswith("CANCEL BALANCE")


# ---------------------------------------------------------------------------
# (c) OI header Lines tab
# ---------------------------------------------------------------------------


def test_the_lines_tab_serializer_carries_the_splits_of_a_split_line(api):
    s = _order(api, [(10, 10, D_A), (20, 6, D_B)])
    _confirm_all(s)
    _save(s, {10: [_sp(4, D_A, on=_on(s, 10)), _sp(6, D_B, on=_on(s, 20))]})
    service = ProjectOrderInquiryService(s.db)
    split_row = service.serialize_rows([_order_row(s.world, s.line[10])])[0]
    plain_row = service.serialize_rows([_order_row(s.world, s.line[20])])[0]
    assert [Decimal(str(x["qty"])) for x in split_row["splits"]] == [4, 6]
    assert [x["delivery_date"] for x in split_row["splits"]] == [
        D_A.isoformat(), D_B.isoformat()]
    assert plain_row["splits"] == []
