"""OI-PRODUCT-FOLLOW S2 (`documentation/plans/scm/PLAN-oi-product-follow-2oct.md`).

The planning board applies a `product_changed` row: the line's order inquiry row is
restated IN PLACE on the new product, exactly the way a qty / date change already is
(`_settle_row_in_place`), with the old code kept as `previous_item_code` ("was X").

Owner rulings 2 Oct (plan R1/R2/R4): the switch happens when CS clicks Confirm in
fulfilment planning (the apply); a link already on the row is KEPT and the change is
flagged with the existing qty/date mechanism (acknowledged -> changed, `changed_at`); the
handover email says "CHANGE ITEM CODE TO <new> (WAS <old>)".

Runs on the real database, rolled back (same fixture as the board-apply suite).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    ACK_CHANGED,
    INQUIRY_CANCELLED,
    INQUIRY_PLACED,
    IV_ORDER,
    OrderInquiryRow,
)
from app.services.project_order_inquiry_service import ProjectOrderInquiryService
from app.services.scm.outstanding_diff import PRODUCT_CHANGED, Change, Line

from .test_order_inquiry_handover_automation import (
    _captured_dispatches,
    _handover_calls,
    _register,
)
from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures reused
    _build,
    _confirm,
    _link,
    _order_row,
    _po_line,
    _rows_of,
    _supplier,
    api,
    world,
)
from .test_planning_changes import (
    _core_line,
    _core_so,
    _line_payload,
    _product,
    _project_line,
    _project_so,
)

DUE = date(2026, 11, 20)


def _swap_fixture(
    api, *, linked: bool = False, acknowledged: bool = False, mirror_stale: bool = False,
    row_code: str | None = None, placed_unlinked: bool = False,
):
    """One line of product A confirmed as a Buy of 10 (one ORDER row), then AutoCount
    swaps the line to product B on the same DtlKey and the ESB raises one
    `product_changed` row. The mirror already names B (S1)."""
    client, world = api
    db = world.db
    old_product = world.product
    new_product = _product(db)
    core_so = _core_so(db, world.company_id)
    core = _core_line(db, core_so, old_product, world.own_wh, qty_ordered="10",
                      required_date=DUE)
    order = _project_so(db, world.project, so_id=core_so.id,
                        autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=old_product, core_line=core)
    db.commit()

    response = _confirm(client, order.id, [_line_payload(line.id, buy_qty="10")])
    assert response.status_code == 200, response.text
    row = _order_row(world, line)
    assert row.item_code == old_product.product_code

    link = None
    if linked:
        _, po_line = _po_line(world, _supplier(world), qty=10, expected_date=DUE)
        link = _link(world, row, po_line, qty=10, document="202609-S0776")
    if acknowledged:
        row.ack_state = ACK_ACKNOWLEDGED
    if row_code is not None:
        row.item_code = row_code
    if placed_unlinked:
        # SO349754 WESERP10B shape: placed on a PO through a path that wrote no link,
        # so `_settle_row_in_place` declines and the netting path runs instead.
        row.state = INQUIRY_PLACED
    db.commit()

    core.product_id = new_product.id
    if not mirror_stale:
        line.product_id = new_product.id
    db.commit()

    change = Change(
        PRODUCT_CHANGED, core_so.so_number, new_product.product_code, "ZZT",
        before=Line(doc_number=core_so.so_number, item_code=old_product.product_code,
                    location="ZZT", qty=10.0, required_date=DUE, row_ref=str(core.id)),
        after=Line(doc_number=core_so.so_number, item_code=new_product.product_code,
                   location="ZZT", qty=10.0, required_date=DUE, row_ref=str(core.id)),
    )
    batch = _build(world, [change], core_so, [str(core.id)])
    assert batch is not None, "a held line's product swap raises a batch"
    return {
        "client": client, "world": world, "order": order, "line": line, "row": row,
        "batch": batch, "old": old_product, "new": new_product, "link": link,
    }


def _apply(fx):
    return _confirm(
        fx["client"], fx["order"].id, [_line_payload(fx["line"].id, buy_qty="10")],
        batch_id=str(fx["batch"].id),
    )


def _live_order_rows(fx):
    return [
        r for r in _rows_of(fx["world"], fx["line"])
        if r.verb == IV_ORDER and r.state != INQUIRY_CANCELLED
    ]


def test_apply_restates_the_oi_row_on_the_new_product_and_keeps_the_old_as_was(api):
    fx = _swap_fixture(api)
    row_id = str(fx["row"].id)

    response = _apply(fx)
    assert response.status_code == 200, response.text
    fx["world"].db.commit()
    fx["world"].db.expire_all()

    live = _live_order_rows(fx)
    assert len(live) == 1, "one OI row per SO line, always"
    row = live[0]
    assert str(row.id) == row_id, "restated in place, never re-raised"
    assert row.item_code == fx["new"].product_code, (
        "S2: the OI line must follow the SO line's new product"
    )
    assert row.previous_item_code == fx["old"].product_code, (
        "S2: the old product stays visible as 'was X'"
    )
    assert Decimal(str(row.qty)) == Decimal("10")
    assert row.delivery_date == DUE
    assert fx["old"].product_code in (row.note or ""), "the note says what it was"
    assert row.previous_qty is None and row.previous_delivery_date is None, (
        "AC-PF-5: a product-only change prints no false 'Was 10 -> Now 10'"
    )


def test_an_acknowledged_row_goes_back_to_to_confirm_on_a_product_swap(api):
    fx = _swap_fixture(api, acknowledged=True)

    response = _apply(fx)
    assert response.status_code == 200, response.text
    fx["world"].db.commit()
    fx["world"].db.expire_all()

    row = _live_order_rows(fx)[0]
    assert row.item_code == fx["new"].product_code
    assert row.ack_state == ACK_CHANGED, "purchasing re-confirms an amended row"
    assert row.changed_at is not None


def test_a_linked_row_keeps_its_link_and_is_flagged_changed(api):
    """R2: the link follows AutoCount and flows through - nothing is unlinked - and the
    change is flagged the way a qty/date change on a linked row already is."""
    fx = _swap_fixture(api, linked=True, acknowledged=True)
    link_id = str(fx["link"].id)

    response = _apply(fx)
    assert response.status_code == 200, response.text
    fx["world"].db.commit()
    fx["world"].db.expire_all()

    row = _live_order_rows(fx)[0]
    assert row.item_code == fx["new"].product_code
    assert row.previous_item_code == fx["old"].product_code
    links = ProjectOrderInquiryService(fx["world"].db)._links_of(row.id)
    assert [str(link.id) for link in links] == [link_id], "R2: the link is kept"
    assert Decimal(str(links[0].qty)) == Decimal("10")
    assert row.ack_state == ACK_CHANGED
    assert row.changed_at is not None


def test_handover_email_says_change_item_code_to_new_was_old(api, monkeypatch):
    """R4: on Confirm the handover line to purchasing names the change, in the REMARK
    beside the qty/date phrases, and prints the NEW code in ITEM CODE."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fx = _swap_fixture(api, acknowledged=True)
    calls.clear()

    response = _apply(fx)
    assert response.status_code == 200, response.text
    fx["world"].db.commit()

    matches = _handover_calls(calls)
    assert matches, "a product change must dispatch a handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    mine = [entry for entry in lines if entry["item_code"] == fx["new"].product_code]
    assert len(mine) == 1, lines
    expected = (
        f"CHANGE ITEM CODE TO {fx['new'].product_code} (WAS {fx['old'].product_code})"
    )
    assert expected in mine[0]["remark"], mine[0]
    assert (mine[0]["was"] or {}).get("item_code") == fx["old"].product_code
    headline = matches[-1]["context"]["handover"]["headline"]
    assert "CHANGE ITEM CODE" in headline, headline


def test_handover_remark_joins_product_and_qty_change():
    """R4, pure: product + qty in one settle read as one REMARK, qty phrase first."""
    from types import SimpleNamespace

    from app.services.project_order_inquiry_service import handover_remark

    row = SimpleNamespace(
        verb=IV_ORDER, qty=Decimal("15"), delivery_date=DUE, cited_document=None,
        note=None, item_code="MWCY7604-SH",
    )
    remark = handover_remark(
        "settled", row, {"qty": Decimal("10"), "item_code": "MWCY7604"}
    )
    assert remark == "ORDER 5, CHANGE ITEM CODE TO MWCY7604-SH (WAS MWCY7604)"


def test_a_line_swapped_before_the_mirror_followed_still_moves_on_confirm(api):
    """SO423414 shape: AutoCount swapped the product BEFORE S1 shipped, so the board's
    mirror line still names the OLD product. The Confirm must still move the OI row."""
    fx = _swap_fixture(api, mirror_stale=True)

    response = _apply(fx)
    assert response.status_code == 200, response.text
    fx["world"].db.commit()
    fx["world"].db.expire_all()

    row = _live_order_rows(fx)[0]
    assert row.item_code == fx["new"].product_code
    assert row.previous_item_code == fx["old"].product_code


def test_a_row_whose_code_is_not_a_catalogue_code_is_never_rewritten(api):
    """AC-PF-5 (review B2): a sheet's own spelling is not read as a product change."""
    fx = _swap_fixture(api, row_code="TEXON SHEET SPELLING 7604")

    response = _apply(fx)
    assert response.status_code == 200, response.text
    fx["world"].db.commit()
    fx["world"].db.expire_all()

    rows = [r for r in _rows_of(fx["world"], fx["line"]) if r.item_code == "TEXON SHEET SPELLING 7604"]
    assert rows, "the sheet-spelled row is still there under its own code"
    assert all(r.previous_item_code is None for r in rows)


def test_a_declined_settle_still_moves_the_product_and_tells_purchasing(api, monkeypatch):
    """Review S2: a lone PLACED row with no link makes `_settle_row_in_place` decline,
    and the netting path runs. The product still follows, with "was", the flag, and one
    handover line saying CHANGE ITEM CODE."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fx = _swap_fixture(api, acknowledged=True, placed_unlinked=True)
    row_id = str(fx["row"].id)
    calls.clear()

    response = _apply(fx)
    assert response.status_code == 200, response.text
    fx["world"].db.commit()
    fx["world"].db.expire_all()

    live = [
        r for r in _rows_of(fx["world"], fx["line"])
        if r.verb == IV_ORDER and r.state != INQUIRY_CANCELLED
    ]
    assert [str(r.id) for r in live] == [row_id], [(r.item_code, r.state) for r in live]
    row = live[0]
    assert row.item_code == fx["new"].product_code
    assert row.previous_item_code == fx["old"].product_code
    assert row.ack_state == ACK_CHANGED

    lines = _handover_calls(calls)[-1]["context"]["handover"]["lines"]
    told = [entry for entry in lines if "CHANGE ITEM CODE" in (entry["remark"] or "")]
    assert len(told) == 1, lines
    assert told[0]["item_code"] == fx["new"].product_code


def test_a_received_link_line_raises_its_replacement_on_the_new_product_with_was(
    api, monkeypatch
):
    """Tester FAIL on 3db1012a (SO381067 / SO396348): the row's document is already
    RECEIVED, so the replan sets the old row aside as "used" (history, keeps its link and
    its old code, R2) and raises a fresh "Replaces N used" row. That fresh row is the
    line's live instruction now, so R1 applies to it: new code, "was OLD", and the email
    says CHANGE ITEM CODE."""
    from app.models.procurement import PurchaseOrderLine

    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fx = _swap_fixture(api, linked=True, acknowledged=True)
    po_line = world.db.get(PurchaseOrderLine, fx["link"].po_line_id)
    po_line.qty_received = po_line.qty_ordered
    world.db.commit()
    calls.clear()

    response = _apply(fx)
    assert response.status_code == 200, response.text
    world.db.commit()
    world.db.expire_all()

    rows = _rows_of(world, fx["line"])
    used = [r for r in rows if r.redirected_to_pool]
    assert [str(r.id) for r in used] == [str(fx["row"].id)], "the received row is set aside"
    assert used[0].item_code == fx["old"].product_code, "history keeps the old code"

    fresh = [
        r for r in rows
        if r.verb == IV_ORDER and r.state != INQUIRY_CANCELLED and not r.redirected_to_pool
    ]
    assert len(fresh) == 1, [(r.item_code, r.state, r.note) for r in rows]
    assert fresh[0].item_code == fx["new"].product_code
    assert fresh[0].previous_item_code == fx["old"].product_code
    assert f"Was item {fx['old'].product_code}" in (fresh[0].note or "")

    handover = _handover_calls(calls)[-1]["context"]["handover"]
    told = [e for e in handover["lines"] if e["item_code"] == fx["new"].product_code]
    assert len(told) == 1, handover["lines"]
    assert (
        f"CHANGE ITEM CODE TO {fx['new'].product_code} (WAS {fx['old'].product_code})"
        in told[0]["remark"]
    ), told[0]
    assert "CHANGE ITEM CODE" in handover["headline"], handover["headline"]


def test_a_plain_qty_settle_writes_no_previous_item_code(api):
    """Kill guard: `previous_item_code` is written only when the product moved."""
    fx = _swap_fixture(api)
    # Undo the swap on the row's side: the row already names the new product, so the
    # apply is a restatement with no product change.
    fx["row"].item_code = fx["new"].product_code
    fx["world"].db.commit()

    response = _apply(fx)
    assert response.status_code == 200, response.text
    fx["world"].db.commit()
    fx["world"].db.expire_all()

    row = _live_order_rows(fx)[0]
    assert row.previous_item_code is None


def test_worklist_payload_carries_previous_item_code(api):
    fx = _swap_fixture(api)
    assert _apply(fx).status_code == 200
    fx["world"].db.commit()

    client = fx["client"]
    res = client.get(
        "/api/v1/project-sales/order-inquiries",
        params={"query": fx["new"].product_code},
    )
    assert res.status_code == 200, res.text
    items = res.json()["data"]
    assert len(items) == 1, res.text
    assert items[0]["item_code"] == fx["new"].product_code
    assert items[0].get("previous_item_code") == fx["old"].product_code


def test_oi_row_model_has_previous_item_code():
    assert hasattr(OrderInquiryRow, "previous_item_code")


def test_a_carried_line_re_raised_on_the_new_product_carries_was(api):
    """Same gap one branch over: CS confirms ANOTHER line of the order, the swapped line
    rides along as a carry and is re-raised on its live product. That re-raise is where
    the switch shows, so it carries "was OLD" too."""
    client, world = api
    db = world.db
    old_product = world.product
    new_product = _product(db)
    core_so = _core_so(db, world.company_id)
    core_1 = _core_line(db, core_so, old_product, world.own_wh, qty_ordered="10",
                        required_date=DUE)
    core_2 = _core_line(db, core_so, old_product, world.own_wh, qty_ordered="5",
                        required_date=DUE)
    order = _project_so(db, world.project, so_id=core_so.id,
                        autocount_doc_no=core_so.so_number)
    line_1 = _project_line(db, order, line_no=1, product=old_product, core_line=core_1)
    line_2 = _project_line(db, order, line_no=2, product=old_product, core_line=core_2)
    db.commit()
    response = _confirm(client, order.id, [
        _line_payload(line_1.id, buy_qty="10"), _line_payload(line_2.id, buy_qty="5"),
    ])
    assert response.status_code == 200, response.text

    core_1.product_id = new_product.id
    line_1.product_id = new_product.id
    from decimal import Decimal as _D
    core_2.qty_ordered = _D("4")
    line_2.qty = _D("4")
    db.commit()

    response = _confirm(client, order.id, [_line_payload(line_2.id, buy_qty="4")])
    assert response.status_code == 200, response.text
    db.commit()
    db.expire_all()

    live = [
        r for r in _rows_of(world, line_1)
        if r.verb == IV_ORDER and r.state != INQUIRY_CANCELLED
    ]
    assert len(live) == 1, [(r.item_code, r.state) for r in _rows_of(world, line_1)]
    assert live[0].item_code == new_product.product_code
    assert live[0].previous_item_code == old_product.product_code, (
        "a carry that moved the row onto the new product must say what it was"
    )
    assert f"Was item {old_product.product_code}" in (live[0].note or "")
