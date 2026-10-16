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
    _link,
    _order_row,
    _po_line,
    _supplier,
    api,
    world,
)
from tests.test_oi_handover_excel import stored  # noqa: F401 - autouse storage stub
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
    assert by_qty[8.0][col] == "SPLIT 5 REMAIN 02/11/2026; 3 DELAY 09/11/2026 to line 30"
    assert by_qty[6.0][col] == "SPLIT 3 DELAY 09/11/2026 from line 20"


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


# ---------------------------------------------------------------------------
# (d) the worklist list endpoint (what the OI Lines tab reads)
# ---------------------------------------------------------------------------


def test_the_worklist_list_row_of_a_split_line_carries_its_splits(api):
    from tests.test_planning_changes import BASE

    s = _order(api, [(10, 10, D_A), (20, 6, D_B)])
    _confirm_all(s)
    _save(s, {10: [
        _sp(4, D_A, on=_on(s, 10)),
        _sp(4, D_B, on=_on(s, 20), remark="site not ready"),
        _sp(2, None, on=_on(s, 10), cancel=True),
    ]})
    listed = s.client.get(f"{BASE}/order-inquiries", params={"query": s.core_so.so_number})
    assert listed.status_code == 200, listed.text
    rows = {r["id"]: r for r in listed.json()["data"]}
    split_row = rows[str(_order_row(s.world, s.line[10]).id)]
    plain_row = rows[str(_order_row(s.world, s.line[20]).id)]

    got = split_row["splits"]
    assert [Decimal(str(x["qty"])) for x in got] == [4, 4, 2], got
    assert [x["delivery_date"] for x in got] == [D_A.isoformat(), D_B.isoformat(), None]
    assert [x["is_cancel"] for x in got] == [False, False, True]
    assert [x["sits_on_line_no"] for x in got] == [10, 20, 10]
    assert got[1]["remark"] == "site not ready"
    assert [x["change"] for x in got] == ["REMAIN", "DELAY", "CANCEL BALANCE"]
    assert plain_row["splits"] == []


def test_taken_remaining_and_suggested_print_once_on_the_expanded_rows(api):
    s = _order(api, [(10, 10, D_A)])
    _confirm_all(s)
    _po, po_line = _po_line(s.world, _supplier(s.world), qty=4, expected_date=date(2026, 10, 20))
    _link(s.world, _order_row(s.world, s.line[10]), po_line, qty=4, document="202610-S0004")
    s.db.commit()
    _save(s, {10: [_sp(4, D_A, on=_on(s, 10)), _sp(6, D_B, on=_on(s, 10))]})
    heads, rows = _sheet_rows(s)
    assert len(rows) == 2, rows
    cols = [heads.index(h) for h in ("TAKEN", "REMAINING", "SUGGESTED")]
    first, second = rows[0], rows[1]
    assert _day(first[5]) == D_A, "the first split row is the earliest date"
    assert first[cols[0]] not in (None, ""), "TAKEN prints on the first split row"
    assert first[cols[1]] not in (None, ""), "REMAINING prints on the first split row"
    for c in cols:
        assert second[c] in (None, ""), f"{heads[c]} is blank on the other split rows: {second[c]!r}"


def test_the_send_to_purchasing_preview_lists_one_line_per_split(api, monkeypatch, stored):
    from app.models.project_so import OrderInquiry
    from app.services.oi_handover_resend import OrderInquiryHandoverResend
    from tests.test_oi_handover_excel import _drain_on_test_connection, _enabled_handover
    from tests.test_order_inquiry_handover_automation import _register

    _client, world = api
    _register(world)
    _drain_on_test_connection(monkeypatch, world)
    _enabled_handover(world)
    s = _order(api, [(10, 10, D_A)])
    _confirm_all(s)
    _save(s, {10: [_sp(4, D_A, on=_on(s, 10)), _sp(6, D_B, on=_on(s, 10))]})
    inquiry = world.db.get(OrderInquiry, _order_row(world, s.line[10]).order_inquiry_id)
    out = OrderInquiryHandoverResend(world.db).preview([str(inquiry.id)], actor_user_id=world.actor)
    assert out["emails"], out
    assert out["emails"][0]["line_count"] == 2, "one email line per split of the split line"


def test_taken_and_remaining_print_the_exact_figures_on_the_first_split_row(api):
    s = _order(api, [(10, 10, D_A)])
    _confirm_all(s)
    _po, po_line = _po_line(s.world, _supplier(s.world), qty=4, expected_date=date(2026, 10, 20))
    _link(s.world, _order_row(s.world, s.line[10]), po_line, qty=4, document="202610-S0004")
    s.db.commit()
    _save(s, {10: [_sp(4, D_A, on=_on(s, 10)), _sp(6, D_B, on=_on(s, 10))]})
    heads, rows = _sheet_rows(s)
    taken, remaining = heads.index("TAKEN"), heads.index("REMAINING")
    first, second = rows[0], rows[1]
    assert float(first[taken]) == 4.0 and float(first[remaining]) == 6.0, (
        first[taken], first[remaining])
    assert second[taken] in (None, "") and second[remaining] in (None, "")


def test_resend_of_a_split_line_that_no_longer_acts_prints_the_current_row(api, monkeypatch, stored):
    """After a delivery the stored split no longer acts: the Send to purchasing preview
    prints the current row (one line), not the stale split plan (two)."""
    from app.models.project_so import OrderInquiry
    from app.services.oi_handover_resend import OrderInquiryHandoverResend
    from tests.test_oi_handover_excel import _drain_on_test_connection, _enabled_handover
    from tests.test_order_inquiry_handover_automation import _register

    _client, world = api
    _register(world)
    _drain_on_test_connection(monkeypatch, world)
    _enabled_handover(world)
    s = _order(api, [(10, 10, D_A)])
    _confirm_all(s)
    _save(s, {10: [_sp(4, D_A, on=_on(s, 10)), _sp(6, D_B, on=_on(s, 10))]})
    core = s.db.get(type(s.line[10]), s.line[10].id).core_sales_order_line_id
    from app.models.order import SalesOrderLine

    s.db.get(SalesOrderLine, core).qty_delivered = Decimal("3")
    s.db.commit()
    inquiry = world.db.get(OrderInquiry, _order_row(world, s.line[10]).order_inquiry_id)
    out = OrderInquiryHandoverResend(world.db).preview([str(inquiry.id)], actor_user_id=world.actor)
    assert out["emails"], out
    assert out["emails"][0]["line_count"] == 1, "the stale split plan is not printed"


def test_resend_after_the_confirm_that_applied_the_split_still_prints_one_line_per_split(
    api, monkeypatch, stored
):
    """State applied: no pending change and the open qty equals the own-line plan (4), the
    normal state after the Confirm that applied the split. The preview prints the split plan."""
    from app.models.order import SalesOrderLine
    from app.models.project_so import OrderInquiry
    from app.services.oi_handover_resend import OrderInquiryHandoverResend
    from tests.test_oi_handover_excel import _drain_on_test_connection, _enabled_handover
    from tests.test_order_inquiry_handover_automation import _register

    _client, world = api
    _register(world)
    _drain_on_test_connection(monkeypatch, world)
    _enabled_handover(world)
    s = _order(api, [(10, 10, D_A)])
    _confirm_all(s)
    _save(s, {10: [_sp(4, D_A, on=_on(s, 10)), _sp(6, D_B, on=None)]})
    # AutoCount cut the line to the own-line plan, no change pending any more
    line = s.line[10]
    s.db.get(SalesOrderLine, line.core_sales_order_line_id).qty_ordered = Decimal("4")
    line.qty = Decimal("4")
    s.db.commit()
    inquiry = world.db.get(OrderInquiry, _order_row(world, line).order_inquiry_id)
    out = OrderInquiryHandoverResend(world.db).preview([str(inquiry.id)], actor_user_id=world.actor)
    assert out["emails"], out
    assert out["emails"][0]["line_count"] == 2, "an applied split still prints its plan"
