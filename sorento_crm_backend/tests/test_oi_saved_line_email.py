"""ORDERBACK-DELTA extension (owner, 13 Oct 2026 22:40): the purchasing email of a board
Confirm has exactly ONE line per SAVED line whose decision is new or changed, its REMARK
saying what was decided (`saved_line_remark`). Carried lines never appear; a saved line
re-approved with no change sends nothing (Q3); a Confirm with no such line sends no email.

Read off the email actually dispatched (post-commit drain, `AutomationService.dispatch_event`
captured), through the board's own Confirm route. Every row is seeded here.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures reused
    BASE,
    _confirm,
    api,
    world,
)
from .test_planning_changes import _core_line, _core_so, _line_payload, _project_line, _project_so
from .test_so_supply_confirmation import _stock

TRIGGER = "order_inquiry_handover"
D_A = date(2026, 11, 2)
D_B = date(2026, 11, 9)


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


def _order(api, qty_a="10", qty_b="6"):
    _client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_a = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty_a,
                        required_date=D_A)
    core_b = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty_b,
                        required_date=D_B)
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line_a = _project_line(db, order, line_no=10, product=world.product, core_line=core_a)
    line_b = _project_line(db, order, line_no=20, product=world.product, core_line=core_b)
    db.commit()
    return order, line_a, line_b


def _lines(email):
    return [(l["item_code"], l["qty"], l["location"], l["remark"]) for l in email["lines"]]


def _post(api, order, payload):
    client, world = api
    response = _confirm(client, order.id, payload)
    assert response.status_code == 200, response.text
    world.db.commit()


def test_a_first_confirm_sends_one_line_per_saved_line_with_its_decision(api, emails):
    _client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=50)
    world.db.commit()
    order, line_a, line_b = _order(api)
    pool = world.pool_wh.warehouse_code
    own = world.own_wh.warehouse_code

    _post(api, order, [
        _line_payload(line_a.id, buy_qty="10"),
        _line_payload(line_b.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}]),
    ])

    assert len(emails) == 1, emails
    code = world.product.product_code
    assert sorted(_lines(emails[0])) == sorted([
        (code, "10", own, "ORDER"),
        (code, "6", own, f"USE {pool}"),
    ]), _lines(emails[0])


def test_a_saved_line_whose_decision_moved_is_the_only_line(api, emails):
    """Line A Buy 10, line B Use BRW 6. Re-Confirm saves only line B, now a Buy: one line,
    ORDER 6; line A (carried) is absent."""
    _client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=50)
    world.db.commit()
    order, line_a, line_b = _order(api)
    _post(api, order, [
        _line_payload(line_a.id, buy_qty="10"),
        _line_payload(line_b.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}]),
    ])
    emails.clear()

    _post(api, order, [_line_payload(line_b.id, buy_qty="6", amend_reason="Keep BRW free.")])

    assert len(emails) == 1, emails
    assert _lines(emails[0]) == [
        (world.product.product_code, "6", world.own_wh.warehouse_code, "ORDER"),
    ]


def test_a_re_approved_unchanged_saved_line_sends_nothing(api, emails):
    _client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=50)
    world.db.commit()
    order, line_a, line_b = _order(api)
    _post(api, order, [
        _line_payload(line_a.id, buy_qty="10"),
        _line_payload(line_b.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}]),
    ])
    emails.clear()

    _post(api, order, [
        _line_payload(line_b.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}]),
    ])

    assert emails == [], [_lines(e) for e in emails]


def test_one_saved_line_is_one_email_line_however_many_rows_it_raised(api, emails):
    """Use BRW 6 + Buy 4 on one line: ONE email line naming both parts, not an ORDER row
    line beside it."""
    _client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=50)
    world.db.commit()
    order, line_a, _line_b = _order(api)
    pool = world.pool_wh.warehouse_code
    own = world.own_wh.warehouse_code

    _post(api, order, [
        _line_payload(line_a.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}],
                      buy_qty="4", amend_reason="Part from BRW, buy the rest."),
    ])

    assert len(emails) == 1, emails
    assert _lines(emails[0]) == [
        (world.product.product_code, "10", own, f"USE {pool} 6 & ORDER 4"),
    ]


def test_a_line_transferred_to_cancel_so_reads_cancel(api, emails):
    from .test_planning_change_apply_on_board import BASE

    client, world = api
    order, line_a, line_b = _order(api)

    response = client.post(f"{BASE}/sales-orders/{order.id}/confirm", json={
        "lines": [_line_payload(line_a.id, buy_qty="10")],
        "cancelled_lines": [{"project_line_id": str(line_b.id), "reason": "Customer dropped it."}],
    })
    assert response.status_code == 200, response.text
    world.db.commit()

    assert len(emails) == 1, emails
    code = world.product.product_code
    assert sorted(_lines(emails[0])) == sorted([
        (code, "10", world.own_wh.warehouse_code, "ORDER"),
        (code, "0", world.own_wh.warehouse_code, "CANCEL"),
    ]), _lines(emails[0])
    cancel = next(l for l in emails[0]["lines"] if l["remark"] == "CANCEL")
    assert cancel["was"] == {"qty": "6"}, "QTY 6 / QTY CHANGE TO 0 / CANCEL"


# --------------------------------------------------------------------------- template


def _migration(name):
    import importlib.util

    path = f"alembic/versions/{name}.py"
    spec = importlib.util.spec_from_file_location(f"zzt_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _template(db):
    """Seed `oihr_0006`'s document (inserted on a blank schema), then convert it with
    `oihr_0007` and `oihr_0008`: the path a real database takes."""
    from app.models.email_template import EmailTemplate

    from .test_order_inquiry_handover_automation import _run_upgrade

    for name in ("oihr_0006_no_remark_column", "oihr_0007_excel_remark", LAYOUT_MIGRATION):
        _run_upgrade(_migration(name), db)
    return (
        db.query(EmailTemplate)
        .filter(EmailTemplate.code == "order_inquiry_handover_default")
        .one()
    )


LAYOUT_MIGRATION = "oihr_0008_so_table_on_top"


def _rows(html):
    """Every row's header or data cell texts, tags stripped, in document order."""
    import re

    if "<!--block:custom_text-->" in html:
        from tests._email_body import authored_html

        html = authored_html(html)
    rows = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row_html, re.S)
        if cells:
            rows.append([re.sub(r"<[^>]+>", "", c).strip() for c in cells])
    return rows


def _context(lines):
    return {
        "handover": {
            "subject_scope": "SO425879", "verbs": ["ORDER"], "headline": "ORDER, USE BRW",
            "orders": [{
                "so_number": "SO425879", "so_date": "10/10/2026", "customer": "KHOO SOON LEE",
                "project": "RESIDENSI A",
            }],
            "lines": lines, "line_count": len(lines),
            "link": "https://crm.test/project-sales/order-inquiries?query=SO425879",
        },
        "actor": {"name": "Aisyah", "email": "cs@example.com"},
        "today": "13/10/2026",
    }


def _line(**over):
    base = {
        "so_date": "10/10/2026", "so_number": "SO425879", "item_code": "SRTWC8826-SC",
        "qty": "4", "delivery_date": "15/11/2026", "customer": "KHOO SOON LEE",
        "project": "RESIDENSI A", "location": "BRW-BB", "remark": "ORDER", "was": None,
    }
    base.update(over)
    return base


def _render(lines):
    from app.services.email_template_service import EmailTemplateService

    from ._pg_fixture import blank_session

    with blank_session() as db:
        template = _template(db)
        return EmailTemplateService(db).render(template, _context(lines))


def test_the_email_reads_as_before_so_table_on_top_lines_with_their_remark_below():
    """Owner hand test 14 Oct 2026: "just like before" (`oihr_0006`): the S/O facts once in
    their own table at the top, then the item lines, each with its REMARK."""
    rendered = _render([_line(), _line(item_code="SRTWCY8826", qty="2", remark="USE BRW")])
    rows = _rows(rendered["body_html"])
    assert rows[0] == ["S/O NO", "SO DATE", "CUSTOMER", "PROJECT"], rows[0]
    assert rows[1] == ["SO425879", "10/10/2026", "KHOO SOON LEE", "RESIDENSI A"], rows[1]
    assert rows[2] == ["LOCATION", "ITEM CODE", "QTY", "DELIVERY", "REMARK"], rows[2]
    assert rows[3] == ["BRW-BB", "SRTWC8826-SC", "4", "15/11/2026", "ORDER"], rows[3]
    assert rows[4] == ["BRW-BB", "SRTWCY8826", "2", "15/11/2026", "USE BRW"], rows[4]
    assert len(rows) == 5, rows
    text = rendered["body_text"]
    assert "LOCATION | ITEM CODE | QTY | DELIVERY | REMARK" in text, text
    assert "BRW-BB | SRTWCY8826 | 2 | 15/11/2026 | USE BRW" in text, text


def test_the_heading_is_one_plain_heading_whatever_the_lines_say():
    """Owner, 14 Oct 2026: one plain heading; the details live only in each line's REMARK."""
    rendered = _render([_line(remark="ORDER BACK")])
    assert "ORDER INQUIRY" in rendered["body_html"]
    assert "ORDER, USE BRW" not in rendered["body_html"]
    assert rendered["body_text"].startswith("ORDER INQUIRY\n"), rendered["body_text"][:60]


def test_a_cut_line_shows_new_qty_beside_its_remark():
    rows = _rows(_render([_line(qty="1", was={"qty": "2"}, remark="CANCEL 1")])["body_html"])
    assert rows[2] == ["LOCATION", "ITEM CODE", "QTY", "NEW QTY", "DELIVERY", "REMARK"], rows[2]
    assert rows[3] == ["BRW-BB", "SRTWC8826-SC", "2", "1", "15/11/2026", "CANCEL 1"], rows[3]


def test_the_layout_migration_downgrades_to_the_previous_document():
    from app.models.email_template import EmailTemplate

    from ._pg_fixture import blank_session
    from .test_order_inquiry_handover_automation import _run_downgrade, _run_upgrade

    with blank_session() as db:
        previous = _migration("oihr_0007_excel_remark")
        latest = _migration(LAYOUT_MIGRATION)
        _run_upgrade(_migration("oihr_0006_no_remark_column"), db)
        _run_upgrade(previous, db)
        _run_upgrade(latest, db)
        row = db.query(EmailTemplate).filter(
            EmailTemplate.code == "order_inquiry_handover_default"
        ).one()
        db.refresh(row)
        assert row.body_text == latest._BODY_TEXT
        _run_downgrade(latest, db)
        db.refresh(row)
        assert row.body_text == previous._BODY_TEXT
        _run_upgrade(latest, db)
        _run_upgrade(latest, db)  # idempotent
        db.refresh(row)
        assert row.body_text == latest._BODY_TEXT


def test_a_buy_replaced_by_stock_says_to_take_the_order_back(api, emails):
    """Line A Buy 10, re-Confirmed as Use BRW 10: purchasing has an order to cancel."""
    _client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=50)
    world.db.commit()
    order, line_a, _line_b = _order(api)
    _post(api, order, [_line_payload(line_a.id, buy_qty="10")])
    emails.clear()
    pool = world.pool_wh.warehouse_code

    _post(api, order, [
        _line_payload(line_a.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}],
                      amend_reason="BRW has it."),
    ])

    assert len(emails) == 1, emails
    assert _lines(emails[0]) == [
        (world.product.product_code, "10", world.own_wh.warehouse_code,
         f"USE {pool}, CANCEL ORDER 10"),
    ]


# ----------------------------------------------- a Borrow reads the line's OWN location
# Owner rule (13 Oct 2026, re-confirmed 14 Oct): every email line telling a Borrow shows the
# SO line's OWN location, and the subject "OI: <location> @ <S/O NO>" uses it too. The
# ORDER BACK row itself keeps its donor `stock_location` (purchasing orders back there).


def _borrow_from(world, donor, qty):
    return [{
        "source": "other_location", "warehouse_id": donor.id, "qty": qty,
        "reason": "Donor can spare it.",
    }]


def _donor(world, tag):
    from .test_planning_changes import _warehouse

    donor = _warehouse(world.db, f"ZZT-{tag}-{world.own_wh.warehouse_code[-4:]}")
    _stock(world.db, world.product, donor, on_hand=100)
    world.db.commit()
    return donor


def test_a_changed_borrow_reads_the_lines_own_location_in_the_email(api, emails):
    from app.models.project_so import INQUIRY_CANCELLED, IV_ORDER_BACK, OrderInquiryRow

    _client, world = api
    donor_x = _donor(world, "DX")
    donor_y = _donor(world, "DY")
    order, line_a, _line_b = _order(api)
    own = world.own_wh.warehouse_code
    code = world.product.product_code

    _post(api, order, [_line_payload(line_a.id, borrow=_borrow_from(world, donor_x, "10"))])
    emails.clear()
    _post(api, order, [_line_payload(
        line_a.id, borrow=_borrow_from(world, donor_y, "10"), amend_reason="Y has it.",
    )])

    assert len(emails) == 1, emails
    assert _lines(emails[0]) == [(code, "10", own, "ORDER BACK")], _lines(emails[0])
    assert emails[0]["subject_scope"].startswith(f"{own} @ "), emails[0]["subject_scope"]
    live = (
        world.db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line_a.id, OrderInquiryRow.verb == IV_ORDER_BACK,
                OrderInquiryRow.state != INQUIRY_CANCELLED)
        .all()
    )
    assert [r.stock_location for r in live] == [donor_y.warehouse_code], "rows keep the donor"


def test_a_rejected_borrow_reads_the_lines_own_location_in_the_email(api, emails):
    from app.models.project_so import IV_ORDER_BACK, OrderInquiryRow

    from .test_fulfilment_line_draft_route import _board, _stage_reject

    client, world = api
    db = world.db
    donor = _donor(world, "DR")
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="2",
                           required_date=D_A)
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()
    own = world.own_wh.warehouse_code
    code = world.product.product_code

    _post(api, order, [_line_payload(line.id, borrow=_borrow_from(world, donor, "2"))])
    emails.clear()
    key = next(
        c["key"] for c in _board(client, core_so)["contributions"] if c["item_code"] == code
    )
    _stage_reject(client, key)
    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [], "rejected_line_ids": [str(line.id)]},
    )
    assert response.status_code == 200, response.text
    db.commit()

    assert len(emails) == 1, emails
    assert _lines(emails[0]) == [(code, "0", own, "CANCEL ALL")], _lines(emails[0])
    assert emails[0]["lines"][0]["was"] == {"qty": "2"}, emails[0]["lines"]
    assert emails[0]["subject_scope"].startswith(f"{own} @ "), emails[0]["subject_scope"]
    row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER_BACK)
        .one()
    )
    assert row.stock_location == donor.warehouse_code, "the row keeps its donor location"


# ------------------------------------- a donor move is not news (reviewer B1, 14 Oct 2026)


def _one_line_order(world, qty):
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty,
                           required_date=D_A)
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()
    return order, line, core_line


def _then_changed_email(api, emails, *, first, second, second_donor_moves, cut_to=None,
                        qty="10"):
    """Confirm `first`, optionally cut the SO line, re-Confirm `second`; the one email line."""
    _client, world = api
    order, line, core_line = _one_line_order(world, qty)
    _post(api, order, [_line_payload(line.id, **first)])
    emails.clear()
    if cut_to is not None:
        core_line.qty_ordered = Decimal(cut_to)
        line.qty = Decimal(cut_to)
        world.db.commit()
    _post(api, order, [_line_payload(line.id, **{"amend_reason": "Changed.", **second})])
    assert len(emails) == 1, emails
    assert len(emails[0]["lines"]) == 1, emails[0]["lines"]
    return emails[0]["lines"][0]


def _same(moved, still, own):
    assert moved["location"] == own and still["location"] == own, (moved, still)
    for key in ("item_code", "qty", "was", "remark"):
        assert moved.get(key) == still.get(key), (key, moved, still)
    assert "CANCEL ORDER" not in moved["remark"] or "CANCEL ORDER" in still["remark"], moved


def test_a_borrow_moved_and_cut_reads_as_the_same_cut_without_the_move(api, emails):
    _client, world = api
    x, y = _donor(world, "CX"), _donor(world, "CY")
    own = world.own_wh.warehouse_code
    kept = _then_changed_email(
        api, emails, first={"borrow": _borrow_from(world, x, "10")},
        second={"borrow": _borrow_from(world, x, "8")}, second_donor_moves=False, cut_to="8",
    )
    moved = _then_changed_email(
        api, emails, first={"borrow": _borrow_from(world, x, "10")},
        second={"borrow": _borrow_from(world, y, "8")}, second_donor_moves=True, cut_to="8",
    )
    _same(moved, kept, own)


def test_a_borrow_moved_with_a_new_buy_split_reads_as_the_same_split_without_the_move(
    api, emails,
):
    _client, world = api
    x, y = _donor(world, "SX"), _donor(world, "SY")
    own = world.own_wh.warehouse_code
    kept = _then_changed_email(
        api, emails,
        first={"borrow": _borrow_from(world, x, "6"), "buy_qty": "4", "amend_reason": "Part from stock."},
        second={"borrow": _borrow_from(world, x, "4"), "buy_qty": "6", "amend_reason": "Part from stock."},
        second_donor_moves=False,
    )
    moved = _then_changed_email(
        api, emails,
        first={"borrow": _borrow_from(world, x, "6"), "buy_qty": "4", "amend_reason": "Part from stock."},
        second={"borrow": _borrow_from(world, y, "4"), "buy_qty": "6", "amend_reason": "Part from stock."},
        second_donor_moves=True,
    )
    _same(moved, kept, own)
