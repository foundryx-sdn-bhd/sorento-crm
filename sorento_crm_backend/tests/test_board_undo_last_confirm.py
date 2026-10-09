"""S2 (#979) - `undo_last_confirm` replay, the record action, refusal at commit.

RED for Phase 2 of `documentation/plans/scm/PLAN-board-undo-last-confirm.md`. Nothing in
this file has an implementation yet:

* `app.services.project_supply_undo_service` does not exist - importing `undo_last_confirm`
  raises `ModuleNotFoundError`, this file's "right reason" for most tests here.
* `app.services.record_actions` does not register `project_sales_order.undo_confirm` yet -
  `get_action(...)` returns `None`, and `POST /api/v1/pending-actions` answers 400 "Unknown
  action" rather than 202.
* `so_supply_decisions.undo_journal` does not exist either (S1's own column), so a decision
  read straight off the ORM has no journal to replay in the first place.

Postgres only, via `tests/_pg_fixture.py::blank_session` (through the `api` fixture imported
from `tests/test_so_supply_confirmation.py`), except the one batch-fork test, which reuses
`tests/test_planning_change_apply_on_board.py`'s own seeding against the REAL local database
(rolled back) - `scm.committed_v` is a view the blank scratch schema does not carry. Every row
is seeded behind the `ZZT` marker; nothing is borrowed from an existing table.

Testing seam agreed in the PLAN ("Testing seams"): exact restore is proven by snapshotting the
nine tables the confirm write-set touches to plain dicts (keyed by pk, `updated_at` dropped)
before the SECOND confirm, then diffing after undo - never asserting field by field, which
would silently miss a table the coder's replay forgot.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.procurement import PurchaseOrder, PurchaseOrderLine
from app.models.project_so import (
    INQUIRY_PLACED,
    IV_ORDER,
    AllocationClaim,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    SOLineAllocation,
    SOSupplyDecision,
    SOSupplyDecisionDraft,
)
from app.models.scm import OrderLinkClaim
from app.models.stock_transfer import StockTransfer
from app.services import project_line_draft_service
from app.services.error_handler import AppException

from ._borrow_mode import borrow_from_order_on
from .test_so_supply_confirmation import (  # noqa: F401  (api is a fixture)
    BASE,
    _core_line,
    _core_so,
    _line_payload,
    _product,
    _project_line,
    _project_so,
    _second_company,
    _stock,
    _suffix,
    _uid,
    _user,
    _warehouse,
    api,
)

MARKER = "zzt-undo-last-confirm"

SNAPSHOT_TABLES = [
    SOSupplyDecision.__table__,
    SOLineAllocation.__table__,
    AllocationClaim.__table__,
    StockTransfer.__table__,
    OrderInquiry.__table__,
    OrderInquiryRow.__table__,
    OrderInquiryLink.__table__,
    OrderLinkClaim.__table__,
    SOSupplyDecisionDraft.__table__,
]


def _snapshot(db, tables=SNAPSHOT_TABLES) -> dict:
    """Every row of `tables`, keyed by table then primary key, `updated_at` dropped.

    `undo_journal` is dropped too (UAC AC-UC-16, amended 17 Sep): R3 clears the
    REINSTATED decision's own journal on undo, so byte-equality on that one column is
    wrong by design - AC-UC-22 (which passes) is the test that pins the clearing itself.
    Only `so_supply_decisions` carries the column; popping it elsewhere is a no-op.
    """
    out: dict = {}
    for table in tables:
        pk_cols = [c.name for c in table.primary_key.columns]
        rows = db.execute(table.select()).mappings().all()
        keyed = {}
        for row in rows:
            row_dict = dict(row)
            row_dict.pop("updated_at", None)
            row_dict.pop("undo_journal", None)
            key = "|".join(str(row_dict[c]) for c in pk_cols)
            keyed[key] = row_dict
        out[f"{table.schema}.{table.name}" if table.schema else table.name] = keyed
    return out


def _po_line(db, world, *, qty, expected_date=None, qty_received="0", line_status="open"):
    po = PurchaseOrder(
        id=_uid(), company_id=world.company_id, po_number=f"ZZT-PO-{_suffix()}",
        issue_date=date.today(), status="active",
    )
    db.add(po)
    db.flush()
    line = PurchaseOrderLine(
        id=_uid(), company_id=world.company_id, purchase_order_id=po.id,
        product_id=world.product.id, warehouse_id=world.own_wh.id,
        qty_ordered=Decimal(str(qty)), qty_received=Decimal(str(qty_received)),
        expected_date=expected_date or date.today(), line_status=line_status,
    )
    db.add(line)
    db.flush()
    return po, line


def _link_with_claim(db, world, row, po_line, *, qty, auto=True, so_number="ZZT-SO"):
    claim = OrderLinkClaim(
        id=_uid(), company_id=world.company_id, so_number=so_number,
        po_number=po_line.purchase_order_id, item_code=world.product.product_code,
        source="order_inquiry", po_line_id=po_line.id,
    )
    db.add(claim)
    db.flush()
    link = OrderInquiryLink(
        id=_uid(), company_id=world.company_id, row_id=row.id, po_line_id=po_line.id,
        document=f"ZZT-{_suffix()}", qty=Decimal(str(qty)), linked_by=world.eling, auto=auto,
        claim_id=claim.id,
    )
    db.add(link)
    db.flush()
    return link, claim


def _confirm_linked_world(api, *, second_buy_qty):
    """One line, twice confirmed: rev1 raises+auto-links it (standing in for the raise-time
    cascade), rev2 re-confirms with `second_buy_qty` - "0" fully removes the link (and frees
    its claim), any other value settles the row in place with a qty/note change.

    Returns everything a test needs to assert against, before AND after the second confirm.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=200)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    order = _project_so(db, world.project, so_id=core_so.id)
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    first = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert first.status_code == 200, first.text
    decision1 = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )
    row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )
    po, po_line = _po_line(db, world, qty=20)
    link, claim = _link_with_claim(db, world, row, po_line, qty=20, so_number=core_so.so_number)
    row.state = INQUIRY_PLACED
    db.commit()

    before = _snapshot(db)

    # The whole-line rule requires every component to sum to the line's OPEN qty (20):
    # the remainder not bought is Reserved from the pool, so `buy_qty` alone can move
    # (20 -> `second_buy_qty`) without the confirm being refused as unbalanced.
    remainder = Decimal("20") - Decimal(second_buy_qty)
    reserve = (
        [{"warehouse_id": world.pool_wh.id, "qty": str(remainder)}] if remainder > 0 else None
    )
    second = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty=second_buy_qty, reserve=reserve)]},
    )
    assert second.status_code == 200, second.text
    decision2 = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.revision_no == 2,
        )
        .one()
    )
    return {
        "client": client, "world": world, "db": db, "order": order, "line": line,
        "decision1": decision1, "decision2": decision2, "row": row, "link": link,
        "claim": claim, "po_line": po_line, "before": before,
    }


# --------------------------------------------------------------------------- AC-UC-16


def test_undo_restores_revision_one_column_for_column(api):
    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    order = fixture["order"]

    from app.services.project_supply_undo_service import undo_last_confirm

    result = undo_last_confirm(db, order, actor_user_id=fixture["world"].eling)
    assert result["revision_no"] == fixture["decision2"].revision_no
    assert result["restored_to"] == fixture["decision1"].revision_no

    after = _snapshot(db)
    assert after == fixture["before"], "every seeded table must read back exactly as before"


# --------------------------------------------------------------------------- AC-B14
# `PLAN-board-reject-on-confirmed-line.md`: a withdrawal that rides alongside a real
# composition (`payload.lines` non-empty) reaches `uncover_lines` THROUGH `confirm()`'s
# own `uncover_line_ids` - journalled exactly as an ordinary reconfirm is, undoable.


def test_undo_of_a_mixed_confirm_restores_the_withdrawn_lines_row_and_the_superseded_revision(
    api,
):
    """A press naming both `lines` (line 2, re-amended) and `rejected_line_ids` (line 1,
    staged rejected) writes revision 2, cancelling line 1's raised OI row. Undoing it must
    put revision 1 back ACTIVE and line 1's row back to `raised` - column for column, the
    same exact-restore seam every other test in this file uses, so a coder's replay that
    forgot the withdrawn line's own row (or its note) is caught rather than merely the
    columns this test happened to name."""
    from app.models.project_so import DECISION_ACTIVE, INQUIRY_CANCELLED, INQUIRY_RAISED

    from .test_fulfilment_line_draft_route import _board, _stage_reject

    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line_1 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    product_2 = _product(db)
    _stock(db, product_2, world.pool_wh, on_hand=100)
    core_line_2 = _core_line(db, core_so, product_2, world.own_wh, qty_ordered="6")
    order = _project_so(db, world.project, so_id=core_so.id)
    line_1 = _project_line(db, order, line_no=10, product=world.product, core_line=core_line_1)
    line_2 = _project_line(db, order, line_no=20, product=product_2, core_line=core_line_2)
    db.commit()

    first = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(line_1.id, buy_qty="10"),
                _line_payload(
                    line_2.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}]
                ),
            ]
        },
    )
    assert first.status_code == 200, first.text
    decision1 = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )
    row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line_1.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )
    assert row.state == INQUIRY_RAISED, "sanity: the row has to start raised"

    key_1 = next(
        contribution["key"]
        for contribution in _board(client, core_so)["contributions"]
        if contribution["item_code"] == world.product.product_code
    )
    _stage_reject(client, key_1)

    before = _snapshot(db)

    second = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line_2.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}]
                )
            ],
            "rejected_line_ids": [str(line_1.id)],
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["rejected_count"] == 1
    db.refresh(row)
    assert row.state == INQUIRY_CANCELLED, "sanity: withdrawn by the second confirm"
    decision2 = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.revision_no == 2,
        )
        .one()
    )

    from app.services.project_supply_undo_service import undo_last_confirm

    result = undo_last_confirm(db, order, actor_user_id=world.eling)
    assert result["revision_no"] == decision2.revision_no
    assert result["restored_to"] == decision1.revision_no

    after = _snapshot(db)
    assert after == before, "every table this write touched must read back exactly as before"

    db.expire_all()
    restored_row = db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row.id).one()
    assert restored_row.state == INQUIRY_RAISED
    active = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == DECISION_ACTIVE,
        )
        .one()
    )
    assert active.id == decision1.id, "revision 1 is active again"


# --------------------------------------------------------------------------- AC-UC-17


def test_undo_of_revision_one_leaves_no_active_decision_and_returns_the_drafts(api):
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    order = _project_so(db, world.project, so_id=core_so.id)
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    key = f"{core_so.id}|10|{world.product.product_code}|bkt"
    project_line_draft_service.save_draft(db, key, decision={"buy_qty": "20"}, actor_user_id=world.eling)
    db.commit()
    draft_id = (
        db.query(SOSupplyDecisionDraft)
        .filter(SOSupplyDecisionDraft.core_line_id == core_line.id)
        .one()
        .id
    )

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert response.status_code == 200, response.text
    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )
    assert (
        db.query(SOSupplyDecisionDraft).filter(SOSupplyDecisionDraft.id == draft_id).first()
        is None
    ), "the confirm cleared the draft - the setup this test needs"

    from app.services.project_supply_undo_service import undo_last_confirm

    result = undo_last_confirm(db, order, actor_user_id=world.eling)
    assert result["restored_to"] is None, "revision 1 has no previous decision"

    assert (
        db.query(SOSupplyDecision).filter(SOSupplyDecision.project_sales_order_id == order.id).count()
        == 0
    )
    assert (
        db.query(OrderInquiryRow).filter(OrderInquiryRow.so_line_id == line.id).count() == 0
    )
    restored_draft = (
        db.query(SOSupplyDecisionDraft).filter(SOSupplyDecisionDraft.id == draft_id).first()
    )
    assert restored_draft is not None, "the planner's draft is back"
    assert restored_draft.decision == {"buy_qty": "20"}


# --------------------------------------------------------------------------- AC-UC-18


def test_undo_re_inserts_a_removed_link_and_its_claim_with_original_ids(api):
    fixture = _confirm_linked_world(api, second_buy_qty="0")
    db = fixture["db"]
    order = fixture["order"]
    link_id = fixture["link"].id
    claim_id = fixture["claim"].id

    assert db.query(OrderInquiryLink).filter(OrderInquiryLink.id == link_id).first() is None
    assert db.query(OrderLinkClaim).filter(OrderLinkClaim.id == claim_id).first() is None

    from app.services.project_supply_undo_service import undo_last_confirm

    undo_last_confirm(db, order, actor_user_id=fixture["world"].eling)

    restored_link = db.query(OrderInquiryLink).filter(OrderInquiryLink.id == link_id).one()
    assert restored_link.po_line_id == fixture["po_line"].id
    assert restored_link.qty == Decimal("20")
    assert restored_link.auto is True

    restored_claim = db.query(OrderLinkClaim).filter(OrderLinkClaim.id == claim_id).one()
    assert restored_claim.po_line_id == fixture["po_line"].id


# --------------------------------------------------------------------------- AC-UC-19


def test_undo_restores_a_settled_rows_note_previous_values_and_ack_stamps(api):
    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    row_id = fixture["row"].id
    before_row = fixture["before"]["projects.order_inquiry_rows"][str(row_id)]

    db.expire_all()
    changed_row = db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row_id).one()
    assert changed_row.qty == Decimal("15"), "the settle actually changed it - the setup this test needs"
    assert changed_row.previous_qty == Decimal("20")

    from app.services.project_supply_undo_service import undo_last_confirm

    undo_last_confirm(db, fixture["order"], actor_user_id=fixture["world"].eling)

    db.expire_all()
    restored_row = db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row_id).one()
    assert restored_row.note == before_row["note"]
    assert restored_row.previous_qty == before_row["previous_qty"]
    assert restored_row.previous_delivery_date == before_row["previous_delivery_date"]
    assert restored_row.ack_state == before_row["ack_state"]
    assert restored_row.acknowledged_by == before_row["acknowledged_by"]
    assert restored_row.acknowledged_at == before_row["acknowledged_at"]
    assert restored_row.changed_at == before_row["changed_at"]
    assert restored_row.supply_decision_id == before_row["supply_decision_id"]


# --------------------------------------------------------------------------- AC-UC-20


def test_undo_clears_the_redirect_flag_and_re_inserts_the_removed_open_links(api):
    """A row linked to BOTH a fully-received document and a still-open one: the received
    document redirects the row to the pool and frees the open link (AC-RL-10..12,
    `_redirect_row_if_received`); undo restores the flag and the open link both."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=200)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    order = _project_so(db, world.project, so_id=core_so.id)
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    first = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert first.status_code == 200, first.text
    decision1 = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )
    row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )
    _po_received, po_line_received = _po_line(
        db, world, qty=15, qty_received="15", line_status="open"
    )
    _po_open, po_line_open = _po_line(db, world, qty=5, qty_received="0", line_status="open")
    link_received, _claim_r = _link_with_claim(
        db, world, row, po_line_received, qty=15, so_number=core_so.so_number
    )
    link_open, _claim_o = _link_with_claim(
        db, world, row, po_line_open, qty=5, so_number=core_so.so_number
    )
    row.state = INQUIRY_PLACED
    db.commit()

    before = _snapshot(db)
    assert before["projects.order_inquiry_rows"][str(row.id)]["redirected_to_pool"] is False

    second = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert second.status_code == 200, second.text

    db.expire_all()
    redirected_row = db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row.id).one()
    assert redirected_row.redirected_to_pool is True, "the setup this test needs"
    assert (
        db.query(OrderInquiryLink).filter(OrderInquiryLink.id == link_open.id).first() is None
    ), "the open link was freed - the setup this test needs"
    assert (
        db.query(OrderInquiryLink).filter(OrderInquiryLink.id == link_received.id).first()
        is not None
    ), "the received link stays exactly as it was"

    from app.services.project_supply_undo_service import undo_last_confirm

    undo_last_confirm(db, order, actor_user_id=world.eling)

    after = _snapshot(db)
    assert after == before


# --------------------------------------------------------------------------- AC-UC-21


def test_undo_of_a_batch_confirm_returns_the_batch_to_pending_and_leaves_the_book():
    """AC-UC-21: R4 - no book rewind. Undo returns the planning-change batch's rows to
    `pending` and clears `applied_at`/`applied_by`/`result_json`, but the AutoCount book
    values (`sales_order_lines.qty_ordered` / `required_date`) the batch already applied
    are left exactly where the confirm put them.

    Reuses `tests/test_planning_change_apply_on_board.py`'s own seeding wholesale, the same
    way `test_board_undo_journal.py::test_a_batch_confirm_journals_the_batch_rows_and_header`
    does - its `world`/`api` are pytest fixtures registered under those names, which would
    collide with this file's own `api` import.
    """
    from app.models.base import company_scope
    from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
    from app.services import project_seed_service
    from app.services.project_service import register_project

    from .test_planning_change_apply_on_board import (
        MARKER as BATCH_MARKER,
        _World,
        _apply_from_board,
        _client as batch_client,
        _form_three,
        _product as batch_product,
        _real_db_session,
        _restore as batch_restore,
        _sorento as batch_sorento,
        _uid as batch_uid,
        _user as batch_user,
        _warehouse as batch_warehouse,
    )

    with _real_db_session() as db:
        company_id = batch_sorento(db)
        project_seed_service.run(db, company_id=company_id)
        actor = batch_user(db, f"{BATCH_MARKER} Cyndi")
        project = register_project(
            db, company_id=company_id, actor_user_id=actor, developer_party_id=None,
            title=f"{BATCH_MARKER} Undo Batch World 2",
        )
        product = batch_product(db)
        pool_wh = batch_warehouse(db, f"ZZT-BRW-{batch_uid()[:4]}", segment="dealer")
        own_wh = batch_warehouse(
            db, f"ZZT-IB-{batch_uid()[:4]}", segment="project", pool_warehouse_id=pool_wh.id
        )
        db.flush()
        db.commit()
        world = _World(db, company_id, actor, project, product, own_wh, pool_wh)

        client, originals = batch_client(db, actor)
        try:
            with company_scope(db, frozenset({company_id})):
                fixture = _form_three((client, world))
                response = _apply_from_board(fixture)
                assert response.status_code == 200, response.text

                core_1 = fixture["core_lines"][0]
                assert core_1.qty_ordered == Decimal("25")

                decision = (
                    db.query(SOSupplyDecision)
                    .filter(SOSupplyDecision.project_sales_order_id == fixture["order"].id)
                    .order_by(SOSupplyDecision.revision_no.desc())
                    .first()
                )
                batch_id = fixture["batch"].id

                from app.services.project_supply_undo_service import undo_last_confirm

                undo_last_confirm(db, fixture["order"], actor_user_id=actor)

                db.expire_all()
                batch_rows = (
                    db.query(PlanningChangeRow)
                    .filter(PlanningChangeRow.batch_id == batch_id)
                    .all()
                )
                assert batch_rows, "the batch's own rows must still exist"
                assert all(row.applied_state == "pending" for row in batch_rows)

                batch = db.query(PlanningChangeBatch).filter(PlanningChangeBatch.id == batch_id).one()
                assert batch.applied_at is None
                assert batch.applied_by is None
                assert batch.result_json is None

                db.refresh(core_1)
                assert core_1.qty_ordered == Decimal("25"), "R4: the book is NOT rewound"
        finally:
            batch_restore(originals)


# --------------------------------------------------------------------------- AC-UC-22


def test_undo_keeps_reinstated_journal(api):
    """AC-R2-21 (`PLAN-scm-oi-handover-r2-undo.md` S4, rewrite of the old R3 "one
    revision back, once" expectation): the reinstated decision's own journal is KEPT,
    not cleared - so it is itself undoable in turn (AC-R2-22), and the board names it
    with `mode = "journal"`."""
    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    order = fixture["order"]

    from app.services.project_supply_undo_service import board_undo_map, undo_last_confirm

    undo_last_confirm(db, order, actor_user_id=fixture["world"].eling)

    db.expire_all()
    reinstated = db.query(SOSupplyDecision).filter(
        SOSupplyDecision.id == fixture["decision1"].id
    ).one()
    assert reinstated.state == "active"
    assert reinstated.undo_journal, (
        "AC-R2-21: the reinstated revision must keep its own journal, not have it cleared"
    )

    # AC-R2-27: the board's own `undo` for this order now names rev1 with mode=journal.
    undo_map = board_undo_map(db, {order.so_id: order.id})
    entry = undo_map.get(order.so_id)
    assert entry is not None, "AC-R2-27/21: the reinstated revision must itself be undoable"
    assert entry["revision_no"] == 1
    assert entry.get("mode") == "journal", (
        f"AC-R2-27: board_undo_map's own dict must carry mode='journal', got {entry}"
    )


# --------------------------------------------------------------------------- AC-UC-23/24/25 (review round: refusal rebuilt on the journal insert set, not raw timestamps)


@pytest.mark.parametrize(
    "arm",
    ["linked_after", "auto_link_after", "actioned_after"],
)
def test_purchasing_action_after_the_confirm_refuses_the_undo(api, arm):
    """AC-UC-23/24 rebuilt for the review round: refusal code renamed `manual_link` ->
    `linked`, and the `auto` flag is now IRRELEVANT to it (contract A) - a link purchasing
    places with `place_on_po` (which the auto cascade also uses under the hood in some
    paths) still refuses undo if it lands after the confirm, whatever `auto` reads. Both
    arms insert a link the confirm's OWN journal never named (so it is not in the
    journal's own insert set for `projects.order_inquiry_links`), timestamped after the
    confirm.
    """
    from app.models.project_so import INQUIRY_ACTIONED

    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    order = fixture["order"]
    world = fixture["world"]
    row = fixture["row"]
    decision2 = fixture["decision2"]

    db.expire_all()
    row = db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row.id).one()
    decision2 = db.query(SOSupplyDecision).filter(SOSupplyDecision.id == decision2.id).one()

    from app.services.project_supply_undo_service import undo_last_confirm

    if arm == "linked_after":
        po, po_line = _po_line(db, world, qty=15)
        db.add(
            OrderInquiryLink(
                id=_uid(), company_id=world.company_id, row_id=row.id, po_line_id=po_line.id,
                document=po.po_number, qty=Decimal("5"), linked_by=world.eling, auto=False,
                linked_at=decision2.confirmed_at + timedelta(minutes=1),
            )
        )
        db.commit()
        with pytest.raises(AppException) as exc_info:
            undo_last_confirm(db, order, actor_user_id=world.eling)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "linked"
    elif arm == "auto_link_after":
        po, po_line = _po_line(db, world, qty=15)
        db.add(
            OrderInquiryLink(
                id=_uid(), company_id=world.company_id, row_id=row.id, po_line_id=po_line.id,
                document=po.po_number, qty=Decimal("5"), linked_by=world.eling, auto=True,
                linked_at=decision2.confirmed_at + timedelta(minutes=1),
            )
        )
        db.commit()
        with pytest.raises(AppException) as exc_info:
            undo_last_confirm(db, order, actor_user_id=world.eling)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "linked", "auto=True must not exempt a post-confirm link"
    else:
        row.state = INQUIRY_ACTIONED
        row.actioned_at = decision2.confirmed_at + timedelta(minutes=1)
        db.commit()
        with pytest.raises(AppException) as exc_info:
            undo_last_confirm(db, order, actor_user_id=world.eling)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "actioned"


def test_the_confirms_own_step3_borrow_link_does_not_refuse(api):
    """AC-UC-25, rebuilt off a REAL confirm that places a step-3 supply borrow, per the
    review round finding: the OLD `linked_at not later than confirmed_at` timestamp test
    is wrong at its root - `confirmed_at` is a PYTHON `datetime.utcnow()` captured near
    the top of `_write_decision`, `linked_at` is POSTGRES `now()` resolved at the link
    row's own INSERT moments later in the SAME request, so a genuine step-3 link can read
    `linked_at > confirmed_at` under the old test and be wrongly refused - exactly what a
    fabricated, forced-equal timestamp could never catch. `linked` (contract A) instead
    checks the link's own id against the journal's insert set for
    `projects.order_inquiry_links`: the confirm's own transaction wrote this link, so its
    id IS in that set, whatever either clock reads.

    Seed shape from `tests/test_project_supply_borrow_row_ack.py:37`: `_raise_one_row` +
    `_open_po_line` give a real open document; the SECOND confirm (the one whose journal
    is read) borrows the line's own residual off that document by its own `supply_key`,
    which `_place_supply_borrows` places for real via `place_on_po_allocations` inside
    the confirm's own transaction.
    """
    from app.services.project_supply_undo_service import undo_last_confirm
    from .test_order_inquiry_handshake import _open_po_line, _raise_one_row

    # `test_order_inquiry_handshake.api`/`.world` are pytest fixtures registered under
    # those exact names (same reasoning as the batch helper above): replicate their
    # bodies directly rather than importing the fixtures, to avoid colliding with this
    # file's own `api`.
    from app.models.base import company_scope
    from app.services import project_seed_service
    from app.services.project_service import register_project

    from .test_order_inquiry_handshake import (
        MARKER as HS_MARKER,
        _client as hs_client,
        _pin_the_plan_horizon,
        _product as hs_product,
        _real_db_session,
        _restore as hs_restore,
        _sorento as hs_sorento,
        _uid as hs_uid,
        _user as hs_user,
        _warehouse as hs_warehouse,
        CS,
    )
    from .test_order_inquiry_handshake import _World as HSWorld

    with _real_db_session() as db:
        company_id = hs_sorento(db)
        project_seed_service.run(db, company_id=company_id)
        cs_user = hs_user(db, f"{HS_MARKER} Eling")
        buyer = hs_user(db, f"{HS_MARKER} Joey")
        project = register_project(
            db, company_id=company_id, actor_user_id=cs_user, developer_party_id=None,
            title=f"{HS_MARKER} Undo Step3 World {hs_uid()[:8]}",
        )
        product = hs_product(db)
        warehouse = hs_warehouse(db, f"ZZT-IB-{hs_uid()[:4]}")
        hs_warehouse(db, f"ZZT-IB-SIB-{hs_uid()[:4]}", pool_warehouse_id=warehouse.id)
        _pin_the_plan_horizon(db, company_id)
        db.flush()
        db.commit()
        world = HSWorld(db, company_id, cs_user, buyer, project, product, warehouse, None)

        client, originals = hs_client(db, cs_user, CS)
        try:
            with company_scope(db, frozenset({company_id})):
                api_pair = (client, world)
                fixture = _raise_one_row(api_pair, qty="10")
                order = fixture["order"]
                line = fixture["line"]
                _po, po_line = _open_po_line(world, qty=10)

                second = client.post(
                    f"{BASE}/sales-orders/{order.id}/confirm",
                    json={
                        "lines": [
                            {
                                "project_line_id": str(line.id),
                                "timely_spo_qty": "0",
                                "reserve": [],
                                "buy_qty": "0",
                                "borrow": [
                                    {
                                        "source": "other_location",
                                        "warehouse_id": str(warehouse.id),
                                        "qty": "10",
                                        "reason": "Step 3 supply borrow off an open PO.",
                                        "supply_key": f"po:{po_line.id}",
                                    }
                                ],
                            }
                        ]
                    },
                )
                assert second.status_code == 200, second.text

                decision = (
                    db.query(SOSupplyDecision)
                    .filter(SOSupplyDecision.project_sales_order_id == order.id)
                    .order_by(SOSupplyDecision.revision_no.desc())
                    .first()
                )
                assert decision.undo_journal, "the step-3 confirm must carry its own journal"

                from app.services.project_supply_undo_service import board_undo_map

                # `board_undo_map` keys by the CORE sales order id.
                core_so_id = str(order.so_id)
                undo_map = board_undo_map(db, {core_so_id: str(order.id)})
                undo_map_refusal = undo_map[core_so_id]["refusal"]
                assert undo_map_refusal is None, (
                    f"the confirm's own step-3 link must not refuse its own undo, got {undo_map_refusal!r}"
                )

                result = undo_last_confirm(db, order, actor_user_id=cs_user)
                assert result["revision_no"] == decision.revision_no
        finally:
            hs_restore(originals)


# --------------------------------------------------------------------------- AC-UC-26


def test_journalless_decision_refuses_non_admin_offers_reconstructed_to_admin(api):
    """AC-R2-30/35 (`PLAN-scm-oi-handover-r2-undo.md` S5, rewrite of the old plain
    "refuses" expectation): a journal-less decision still refuses `undo_last_confirm`
    itself (nothing to replay), but the BOARD offers a best-effort `reconstructed` undo
    for it - visible ONLY to an admin/superadmin requester. A non-admin gets `undo:
    null` for that order, same as no active decision at all."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    # `so_id=core_so.id` (review round, captain's diagnosis): the old pre-lane test
    # this rewrites never called the board route, so it never needed the mirror
    # wired to its core order. Now that this test ALSO reads `GET .../board` by
    # `core_so.so_number`, an unwired order is never found as an adopted mirror at
    # all - `undo` reads None for every actor, and the non-admin half only "passes"
    # because there is nothing there to see, not because the admin gate held.
    order = _project_so(db, world.project, so_id=core_so.id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    # A decision minted directly (no board route, no journal) - stands in for a pre-lane
    # revision or one minted by `uncover_lines`.
    decision = SOSupplyDecision(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        revision_no=1, state="active",
        line_snapshots=[{"line_no": 10, "project_line_id": line.id}],
        confirmed_by=world.eling, confirmed_at=datetime.utcnow(),
    )
    db.add(decision)
    db.commit()

    from app.services.project_supply_undo_service import undo_last_confirm

    with pytest.raises(AppException) as exc_info:
        undo_last_confirm(db, order, actor_user_id=world.eling)
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "no_journal"

    # Non-admin: the board never offers a reconstructed undo for this order.
    response = client.get(
        f"{BASE}/fulfilment-planning/board", params={"orders": core_so.so_number}
    )
    assert response.status_code == 200, response.text
    order_standing = next(
        o for o in response.json()["orders"] if o["so_number"] == core_so.so_number
    )
    assert order_standing["undo"] is None, (
        "AC-R2-30: a non-admin must not see a reconstructed undo entry"
    )

    # Admin: the board offers it, named mode="reconstructed".
    from app.services.user_service import UserPermissionService

    original_role_slugs = UserPermissionService.get_user_role_slugs
    UserPermissionService.get_user_role_slugs = lambda self, uid: {"admin"}
    try:
        response = client.get(
            f"{BASE}/fulfilment-planning/board", params={"orders": core_so.so_number}
        )
    finally:
        UserPermissionService.get_user_role_slugs = original_role_slugs
    assert response.status_code == 200, response.text
    order_standing = next(
        o for o in response.json()["orders"] if o["so_number"] == core_so.so_number
    )
    assert order_standing["undo"] is not None, (
        "AC-R2-30: an admin must see the reconstructed undo entry"
    )
    assert order_standing["undo"]["mode"] == "reconstructed"
    assert order_standing["undo"]["revision_no"] == 1


# --------------------------------------------------------------------------- AC-UC-27


def test_the_record_action_is_registered_reversible_with_confirms_permission_and_commits_on_lapse(
    api,
):
    from app.models.sla import SlaFormAction
    from app.services.form_action_grace import WINDOW_REVERSIBLE
    from app.services.form_action_registry import get_action
    from app.services.form_action_service import FormActionService

    action = get_action("project_sales_order.undo_confirm")
    assert action is not None, "the record action must be registered"
    assert action.entity_types == ("project_sales_order",)
    assert action.window == WINDOW_REVERSIBLE
    assert action.permission == "projects.projects.edit"

    client, world = api
    db = world.db
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    confirm = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert confirm.status_code == 200, confirm.text
    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )

    park = client.post(
        "/api/v1/pending-actions",
        json={
            "action_key": "project_sales_order.undo_confirm",
            "entity_type": "project_sales_order",
            "entity_id": str(order.id),
            "payload": {"decision_id": str(decision.id)},
        },
    )
    assert park.status_code == 202, park.text
    parked = park.json()

    db.query(SlaFormAction).filter(SlaFormAction.id == parked["id"]).update(
        {"commit_at": datetime.utcnow() - timedelta(seconds=1)}, synchronize_session=False,
    )
    db.commit()

    outcome = FormActionService(db).commit_due()
    assert outcome["committed"] >= 1, "the sweep must commit an undo nobody is watching"

    db.expire_all()
    assert (
        db.query(SOSupplyDecision).filter(SOSupplyDecision.project_sales_order_id == order.id).count()
        == 0
    ), "revision 1's own undo leaves the order with no active decision"


# --------------------------------------------------------------------------- AC-UC-28


def test_a_confirm_written_during_the_countdown_makes_the_undo_refuse_superseded(api):
    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    order = fixture["order"]
    world = fixture["world"]

    from app.services.project_supply_undo_service import undo_last_confirm

    # The pending action was created naming decision1 - stale by the time it commits,
    # because decision2 (the newer confirm) is now the active one.
    with pytest.raises(AppException) as exc_info:
        undo_last_confirm(
            db, order, actor_user_id=world.eling, expected_decision_id=str(fixture["decision1"].id)
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "superseded"


# --------------------------------------------------------------------------- AC-UC-29


def test_undo_writes_an_audit_delete_for_the_undone_decision(api):
    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    order = fixture["order"]
    decision2_id = fixture["decision2"].id

    from app.models.audit import AuditLog

    from app.services.project_supply_undo_service import undo_last_confirm

    undo_last_confirm(db, order, actor_user_id=fixture["world"].eling)

    audit_row = (
        db.query(AuditLog)
        .filter(
            AuditLog.entity_type == "project_so_supply_decisions",
            AuditLog.entity_id == str(decision2_id),
            AuditLog.action == "DELETE",
        )
        .first()
    )
    assert audit_row is not None, "the undone decision's own DELETE must be audited"


# --------------------------------------------------------------------------- AC-UC-30


def test_a_view_only_user_cannot_create_the_undo_action(api):
    from app.models.sla import SlaFormAction
    from app.services.user_service import UserPermissionService

    client, world = api
    db = world.db
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    confirm = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert confirm.status_code == 200, confirm.text
    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )

    original = UserPermissionService.check_user_has_permission
    try:
        UserPermissionService.check_user_has_permission = (
            lambda self, uid, slug: slug != "projects.projects.edit"
        )
        response = client.post(
            "/api/v1/pending-actions",
            json={
                "action_key": "project_sales_order.undo_confirm",
                "entity_type": "project_sales_order",
                "entity_id": str(order.id),
                "payload": {"decision_id": str(decision.id)},
            },
        )
        assert response.status_code == 403, response.text
    finally:
        UserPermissionService.check_user_has_permission = original

    assert (
        db.query(SlaFormAction)
        .filter(SlaFormAction.source_entity_id == str(order.id))
        .count()
        == 0
    ), "no pending row was parked"


# --------------------------------------------------------------------------- AC-UC-31


def test_another_companys_user_gets_404_on_undo():
    """The record action's own `execute` resolves the order through
    `ProjectSupplyService.get_order` under company scope (PLAN "Permissions and scope") -
    called directly here (bypassing the pending-action machinery) so this test does not
    depend on the exact private wiring inside `record_actions.py`, only on the CONTRACT it
    owes: a foreign company's order 404s before anything changes.
    """
    from app.models.base import company_scope
    from app.services import project_seed_service
    from app.services.form_action_registry import get_action
    from app.services.project_service import register_project

    from .test_so_supply_confirmation import _client, _restore, _sorento
    from ._pg_fixture import blank_session

    with blank_session() as db:
        sorento_id = _sorento(db)
        other_id = _second_company(db)
        project_seed_service.run(db, company_id=sorento_id)
        project_seed_service.run(db, company_id=other_id)

        eling = _user(db, f"{MARKER} Eling")
        farah = _user(db, f"{MARKER} Farah")
        other_project = register_project(
            db, company_id=other_id, actor_user_id=farah, developer_party_id=None,
            title=f"{MARKER} Mocha Residences",
        )
        other_product = _product(db)
        other_wh = _warehouse(db, f"ZZT-OTHWH-{_suffix()}")
        other_core_so = _core_so(db, other_id)
        other_core_line = _core_line(
            db, other_core_so, other_product, other_wh, qty_ordered="10"
        )
        other_order = _project_so(db, other_project, so_id=other_core_so.id)
        other_line = _project_line(
            db, other_order, line_no=10, product=other_product, core_line=other_core_line
        )
        db.commit()

        client, originals = _client(db, farah)
        try:
            with company_scope(db, frozenset({other_id})):
                confirm = client.post(
                    f"{BASE}/sales-orders/{other_order.id}/confirm",
                    json={"lines": [_line_payload(other_line.id, buy_qty="10")]},
                )
                assert confirm.status_code == 200, confirm.text
                decision = (
                    db.query(SOSupplyDecision)
                    .filter(SOSupplyDecision.project_sales_order_id == other_order.id)
                    .one()
                )
        finally:
            _restore(originals)

        action = get_action("project_sales_order.undo_confirm")
        assert action is not None, "the record action must be registered"

        with company_scope(db, frozenset({sorento_id})):
            with pytest.raises(AppException) as exc_info:
                action.execute(
                    db,
                    {"entity_id": str(other_order.id), "decision_id": str(decision.id)},
                )
            assert exc_info.value.status_code == 404

        db.expire_all()
        # The read has to run inside the OWNING company's own scope, the same as the
        # seed above: a fail-closed scope filter (no scope active at all) returns zero
        # rows for every company, and `.one()` would raise regardless of whether the
        # 404 above left the decision untouched or not.
        with company_scope(db, frozenset({other_id})):
            untouched = (
                db.query(SOSupplyDecision)
                .filter(SOSupplyDecision.project_sales_order_id == other_order.id)
                .one()
            )
            assert untouched.state == "active"
            assert untouched.revision_no == 1


# --------------------------------------------------------------------------- review round: new arms 4/5


def test_an_actioned_row_before_the_confirm_on_an_untouched_line_does_not_refuse(api):
    """New arm (review round, contract A): an already-actioned row on a line the
    confirm never named must not refuse undo - only a row actioned AFTER the confirm,
    or newly actioned BY it, blocks undo."""
    from app.models.project_so import INQUIRY_ACTIONED, OrderInquiry

    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    order = fixture["order"]
    world = fixture["world"]
    decision2 = fixture["decision2"]

    other_core_so = _core_so(db, world.company_id)
    other_core_line = _core_line(
        db, other_core_so, world.product, world.own_wh, qty_ordered="5"
    )
    other_line = _project_line(
        db, order, line_no=99, product=world.product, core_line=other_core_line
    )
    inquiry = (
        db.query(OrderInquiry)
        .filter(OrderInquiry.project_sales_order_id == order.id)
        .first()
    )
    other_row = OrderInquiryRow(
        id=_uid(), company_id=world.company_id, order_inquiry_id=inquiry.id,
        so_line_id=other_line.id, item_code="ZZT-UNTOUCHED", qty=Decimal("5"),
        verb=IV_ORDER, state=INQUIRY_ACTIONED, actioned_by=world.eling,
        actioned_at=decision2.confirmed_at - timedelta(days=1),
    )
    db.add(other_row)
    db.commit()

    from app.services.project_supply_undo_service import undo_last_confirm

    result = undo_last_confirm(db, order, actor_user_id=world.eling)
    assert result["revision_no"] == decision2.revision_no


def test_the_confirms_own_cascade_restamp_of_an_actioned_row_does_not_refuse(api):
    """New arm (review round, contract A): a row already ACTIONED before this confirm,
    whose `actioned_at` this confirm's own cascade re-stamps (the journal's own update
    entry for it carries old state `actioned`), must not refuse undo - it was not newly
    actioned by anyone AFTER the confirm, only re-touched by the confirm's own write.

    Built directly against the decision + journal shape rather than hunting the exact
    cascade code path that re-stamps an already-actioned row within one confirm - the
    review round's own contract is stated at this boundary
    (`_refusal_reason`/journal-old-state), and it reads exactly this shape regardless of
    which caller produced it.
    """
    from app.models.project_so import INQUIRY_ACTIONED, OrderInquiry

    client, world = api
    db = world.db
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    inquiry = OrderInquiry(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        state="raised", inquiry_no=f"ZZT-OI-{_uid()[:8]}",
    )
    db.add(inquiry)
    db.flush()
    old_actioned_at = datetime.utcnow() - timedelta(days=1)
    row = OrderInquiryRow(
        id=_uid(), company_id=world.company_id, order_inquiry_id=inquiry.id,
        so_line_id=line.id, item_code="ZZT-RESTAMP", qty=Decimal("20"),
        verb=IV_ORDER, state=INQUIRY_ACTIONED, actioned_by=world.eling,
        actioned_at=old_actioned_at,
    )
    db.add(row)
    db.commit()

    confirmed_at = datetime.utcnow()
    # The confirm's own cascade re-stamps `actioned_at` on this SAME row, in the same
    # transaction the decision below is minted in - a Python clock a few instants after
    # `confirmed_at`, the exact clock-mismatch shape a raw timestamp check cannot read
    # correctly (see the step-3 test above).
    row.actioned_at = confirmed_at + timedelta(seconds=1)
    db.flush()

    decision_id = _uid()
    decision = SOSupplyDecision(
        id=decision_id, company_id=world.company_id, project_sales_order_id=order.id,
        revision_no=1, state="active",
        line_snapshots=[{"line_no": 10, "project_line_id": line.id}],
        confirmed_by=world.eling, confirmed_at=confirmed_at,
        undo_journal=[
            {
                "seq": 1, "op": "update", "table": "projects.order_inquiry_rows",
                "pk": str(row.id),
                "old": {"actioned_at": old_actioned_at.isoformat(), "state": "actioned"},
            },
            {
                "seq": 1, "op": "insert", "table": "projects.so_supply_decisions",
                "pk": decision_id, "old": None,
            },
        ],
    )
    db.add(decision)
    db.commit()

    from app.services.project_supply_undo_service import undo_last_confirm

    result = undo_last_confirm(db, order, actor_user_id=world.eling)
    assert result["revision_no"] == 1


# --------------------------------------------------------------------------- review round: donor sweep


def test_undo_refuses_when_purchasing_linked_a_donor_orders_row_after_the_confirm(api):
    """Contract B (review round): the refusal sweep runs over EVERY order the journal
    touched, donor orders included - a link purchasing places on the DONOR's own raised
    row after a cross-project borrow confirm must refuse the BORROWER's own undo too,
    since undoing it would silently reinstate the donor's superseded decision underneath
    a placement purchasing has since made.

    Precedent: `tests/test_so_supply_confirmation.py:957`
    (`test_cross_project_borrow_writes_an_accepted_claim_directly_with_no_requested_state`).
    """
    from app.models.project_so import IV_ORDER
    from app.services.project_service import register_project

    client, world = api
    db = world.db
    borrow_from_order_on(db)
    donor_wh = _warehouse(db, f"ZZT-DNRX-{_suffix()}")
    _stock(db, world.product, donor_wh, on_hand=100)
    donor_project = register_project(
        db, company_id=world.company_id, actor_user_id=world.eling, developer_party_id=None,
        title=f"{MARKER} Donor Rights",
    )
    donor_core_so = _core_so(db, world.company_id)
    donor_core_line_1 = _core_line(
        db, donor_core_so, world.product, donor_wh, qty_ordered="40"
    )
    donor_core_line_2 = _core_line(
        db, donor_core_so, world.product, donor_wh, qty_ordered="20"
    )
    donor_pso = _project_so(db, donor_project, so_id=donor_core_so.id)
    donor_line_1 = _project_line(
        db, donor_pso, line_no=10, product=world.product, core_line=donor_core_line_1
    )
    donor_line_2 = _project_line(
        db, donor_pso, line_no=20, product=world.product, core_line=donor_core_line_2
    )
    db.commit()

    donor_confirm = client.post(
        f"{BASE}/sales-orders/{donor_pso.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    donor_line_1.id, reserve=[{"warehouse_id": donor_wh.id, "qty": "40"}]
                ),
                _line_payload(donor_line_2.id, buy_qty="20"),
            ]
        },
    )
    assert donor_confirm.status_code == 200, donor_confirm.text

    borrower_order = _project_so(db, world.project)
    borrower_core_so = _core_so(db, world.company_id)
    borrower_core_line = _core_line(
        db, borrower_core_so, world.product, world.own_wh, qty_ordered="15"
    )
    borrower_line = _project_line(
        db, borrower_order, line_no=10, product=world.product, core_line=borrower_core_line
    )
    db.commit()

    borrow_response = client.post(
        f"{BASE}/sales-orders/{borrower_order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    borrower_line.id,
                    borrow=[
                        {
                            "source": "other_project",
                            "warehouse_id": donor_wh.id,
                            "donor_project_id": donor_project.id,
                            "donor_core_line_id": donor_core_line_1.id,
                            "qty": "15",
                            "reason": "Donor Rights lending surplus.",
                        }
                    ],
                )
            ]
        },
    )
    assert borrow_response.status_code == 200, borrow_response.text

    borrower_decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == borrower_order.id)
        .one()
    )
    donor_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == donor_line_2.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )
    po, po_line = _po_line(db, world, qty=20)
    db.add(
        OrderInquiryLink(
            id=_uid(), company_id=world.company_id, row_id=donor_row.id, po_line_id=po_line.id,
            document=po.po_number, qty=Decimal("20"), linked_by=world.eling, auto=False,
            linked_at=borrower_decision.confirmed_at + timedelta(minutes=1),
        )
    )
    db.commit()

    before = _snapshot(db)
    from app.services.project_supply_undo_service import undo_last_confirm

    with pytest.raises(AppException) as exc_info:
        undo_last_confirm(db, borrower_order, actor_user_id=world.eling)
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "linked"
    assert _snapshot(db) == before, "a refused undo must change nothing"


# --------------------------------------------------------------------------- review round: project rights


def test_undo_requires_the_confirmers_project_rights(api):
    """Contract C (review round): undo re-runs Confirm's own per-project authorisation
    (`_assert_can_act_on` shape: `assert_can_edit_project`) at EXECUTE time, for the
    user requesting the undo - not only at park time. A user who holds
    `projects.projects.edit` globally but is neither the project's owner nor an
    approved collaborator gets the same 403/404 Confirm itself would give, and nothing
    changes.
    """
    from app.services.project_service import assert_can_edit_project, get_project_or_404

    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    order = fixture["order"]
    world = fixture["world"]

    outsider = _user(db, f"{MARKER} Outsider")
    db.commit()

    # Sanity: Confirm's OWN check would refuse this exact user too - same project, no
    # ownership, no collaboration.
    project = get_project_or_404(db, world.project.id)
    with pytest.raises(AppException) as confirm_exc:
        assert_can_edit_project(db, project, outsider, {"projects.projects.edit"})
    assert confirm_exc.value.status_code == 403

    before = _snapshot(db)
    from app.services.project_supply_undo_service import undo_last_confirm

    with pytest.raises(AppException) as exc_info:
        undo_last_confirm(db, order, actor_user_id=outsider)
    assert exc_info.value.status_code in (403, 404)
    assert _snapshot(db) == before


# --------------------------------------------------------------------------- review round: audit never leaks the journal


def test_the_journal_never_reaches_audit_logs(api):
    """Contract D (review round): `undo_journal` must never appear in `audit_logs`,
    neither on confirm (the decision's own CREATE/UPDATE audit rows) nor on undo (the
    DELETE row AC-UC-29 pins) - the audit trail names WHAT happened, not a raw replay
    script.

    `register_audit_listeners()` is called explicitly: the TestClient here never runs
    FastAPI's lifespan, so nothing guarantees the listener that writes `audit_logs` is
    installed before this test runs (same note `test_order_inquiry_handover_automation.
    py`'s `_register` makes for the post-commit dispatch listener); idempotent, so a
    prior test in the same process having already called it changes nothing.
    """
    from app.models.audit import AuditLog
    from app.services.audit_service import register_audit_listeners

    register_audit_listeners()

    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    order = fixture["order"]
    world = fixture["world"]

    from app.services.project_supply_undo_service import undo_last_confirm

    undo_last_confirm(db, order, actor_user_id=world.eling)
    db.commit()

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "project_so_supply_decisions")
        .all()
    )
    assert rows, "the decision's own audit trail must exist"
    for row in rows:
        for payload in (row.old_values, row.new_values):
            if not payload:
                continue
            assert "undo_journal" not in payload, (
                f"audit row {row.id} ({row.action}) leaked undo_journal"
            )


# --------------------------------------------------------------------------- review round: pass 2 skips a same-confirm insert+delete


def test_a_link_created_and_removed_inside_one_confirm_does_not_come_back(api):
    """Contract E (review round): pass 2 of replay must skip a pk this SAME confirm
    both inserted and later deleted - re-inserting it would bring back a row that
    never existed before this confirm at all.

    Constructed directly against `UndoJournal` (rather than the exact rare business
    path that both drafts and unlinks the SAME placement inside one write - the closest
    natural precedents, `tests/test_order_inquiry_draft_links.py`'s raise-time cascade
    plus `tests/test_fulfilment_board.py:7577`'s received-document redirect, both need
    TWO separate confirms to reach a removal at all, never one): a link is added then
    removed within the SAME `UndoJournal` block, exactly the shape one confirm's own
    insert-then-delete of a placement would leave in the journal.
    """
    from app.services.project_supply_undo_service import UndoJournal, undo_last_confirm

    client, world = api
    db = world.db
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    first = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert first.status_code == 200, first.text
    decision1 = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )
    row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )
    po, po_line = _po_line(db, world, qty=20)

    decision2_id = _uid()
    with UndoJournal(db) as journal:
        # The partial unique index (one ACTIVE decision per order) requires the
        # supersede to flush BEFORE the new active row is inserted - the same order
        # `_write_decision` itself uses.
        decision1.state = "superseded"
        decision1.superseded_at = datetime.utcnow()
        decision1.superseded_reason = "Reconfirmed by CS."
        db.flush()
        decision2 = SOSupplyDecision(
            id=decision2_id, company_id=world.company_id, project_sales_order_id=order.id,
            revision_no=2, state="active",
            line_snapshots=[{"line_no": 10, "project_line_id": line.id}],
            confirmed_by=world.eling, confirmed_at=datetime.utcnow(),
            supersedes_id=decision1.id,
        )
        db.add(decision2)
        db.flush()
        link = OrderInquiryLink(
            id=_uid(), company_id=world.company_id, row_id=row.id, po_line_id=po_line.id,
            document=po.po_number, qty=Decimal("20"), linked_by=world.eling, auto=True,
        )
        db.add(link)
        db.flush()
        link_id = link.id
        db.delete(link)
        db.flush()
    journal.attach(decision2)
    db.commit()

    result = undo_last_confirm(db, order, actor_user_id=world.eling)
    assert result["revision_no"] == 2

    assert (
        db.query(OrderInquiryLink).filter(OrderInquiryLink.id == link_id).first() is None
    ), "a link this confirm both created and removed must not come back on undo"


# --------------------------------------------------------------------------- review round: decision_id is required


def test_the_undo_action_needs_a_decision_id(api):
    """Contract G (review round): creating the pending action without
    `payload.decision_id` is refused with 400, not parked - undo has nothing to check
    `expected_decision_id` against otherwise (AC-UC-28's own guard)."""
    client, world = api
    db = world.db
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    confirm = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert confirm.status_code == 200, confirm.text

    response = client.post(
        "/api/v1/pending-actions",
        json={
            "action_key": "project_sales_order.undo_confirm",
            "entity_type": "project_sales_order",
            "entity_id": str(order.id),
            "payload": {},
        },
    )
    assert response.status_code == 400, response.text

    from app.models.sla import SlaFormAction

    assert (
        db.query(SlaFormAction)
        .filter(SlaFormAction.source_entity_id == str(order.id))
        .count()
        == 0
    )


# --------------------------------------------------------------------------- follow-up: stored actioned_at, idempotent re-insert, park-time refusal


def test_a_confirm_that_restamps_actioned_at_on_an_actioned_row_does_not_refuse(api):
    """New contract, follow-up to the review round's `actioned` refusal (contract A):
    when the journal's own `old` dict for a row carries an `actioned_at` key but NO
    `state` key - some OTHER column changed under this confirm's own write, `state`
    did not - the refusal must compare the JOURNAL'S OWN stored `old.actioned_at`
    against `confirmed_at`, not the live column. The live value can be re-stamped by
    a later, unrelated write between the confirm and the undo check, exactly the
    clock-mismatch shape `test_the_confirms_own_step3_borrow_link_does_not_refuse`
    documents for `linked`; the `state`-key branch right above already solves it for
    a re-actioned row, this closes the same gap for an `actioned_at`-only touch.

    Today `_grouped_refusals` only reads `old["state"]`; absent that key it falls
    through straight to the LIVE `actioned_at` column (`_grouped_refusals`'s own
    "Fall back to the same clock test an untouched row gets" branch), so this is
    refused - wrongly, since the row's STORED old value predates the confirm and
    only something else re-stamped the live column afterward.

    Built directly against the decision + journal shape, same as
    `test_the_confirms_own_cascade_restamp_of_an_actioned_row_does_not_refuse` above,
    minus the `state` key.
    """
    from app.models.project_so import INQUIRY_ACTIONED, OrderInquiry

    client, world = api
    db = world.db
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    inquiry = OrderInquiry(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        state="raised", inquiry_no=f"ZZT-OI-{_uid()[:8]}",
    )
    db.add(inquiry)
    db.flush()
    old_actioned_at = datetime.utcnow() - timedelta(days=1)
    row = OrderInquiryRow(
        id=_uid(), company_id=world.company_id, order_inquiry_id=inquiry.id,
        so_line_id=line.id, item_code="ZZT-ACTIONED-AT-ONLY", qty=Decimal("20"),
        verb=IV_ORDER, state=INQUIRY_ACTIONED, actioned_by=world.eling,
        actioned_at=old_actioned_at,
    )
    db.add(row)
    db.commit()

    confirmed_at = datetime.utcnow()
    # The LIVE column is re-stamped to AFTER `confirmed_at`, by something other than
    # a `state` change - today's clock fallback reads exactly this and refuses; the
    # journal's own captured `old.actioned_at` below is what the new contract wants
    # compared instead.
    row.actioned_at = confirmed_at + timedelta(seconds=1)
    db.flush()

    decision_id = _uid()
    decision = SOSupplyDecision(
        id=decision_id, company_id=world.company_id, project_sales_order_id=order.id,
        revision_no=1, state="active",
        line_snapshots=[{"line_no": 10, "project_line_id": line.id}],
        confirmed_by=world.eling, confirmed_at=confirmed_at,
        undo_journal=[
            {
                "seq": 1, "op": "update", "table": "projects.order_inquiry_rows",
                "pk": str(row.id),
                # No "state" key: only `actioned_at` changed under the confirm's own
                # write, and its STORED old value is well before `confirmed_at`.
                "old": {"actioned_at": old_actioned_at.isoformat()},
            },
            {
                "seq": 1, "op": "insert", "table": "projects.so_supply_decisions",
                "pk": decision_id, "old": None,
            },
        ],
    )
    db.add(decision)
    db.commit()

    from app.services.project_supply_undo_service import undo_last_confirm

    result = undo_last_confirm(db, order, actor_user_id=world.eling)
    assert result["revision_no"] == 1


def test_replay_re_insert_is_idempotent_when_the_row_still_exists(api):
    """New contract, follow-up to Contract E (`test_a_link_created_and_removed_
    inside_one_confirm_does_not_come_back` above): a delete-journal entry whose row
    still exists in the table at undo time - a savepoint that rolled the delete back
    after the journal had already captured it, or any other reason the row survived -
    must not raise a duplicate-key error on replay. Undo succeeds, and the row exists
    exactly once afterward (idempotent), not zero and not two.

    Today pass 2 of `_replay` ("re-insert every row this confirm deleted, skipping a
    pk this SAME confirm both inserted and later deleted") has no existence check at
    all for the pk it is not skipping - it blindly `INSERT`s the captured old values,
    so a pk already present ends in an `IntegrityError` that `_run_with_retries`
    cannot resolve by reordering (a genuine unique-constraint violation, not an
    FK-ordering one), and re-raises it uncaught after exhausting its retries.

    Built with the REAL `UndoJournal` capture (same tool
    `test_a_link_created_and_removed_inside_one_confirm_does_not_come_back` uses),
    so the delete entry's `old` dict is byte-for-byte what a real confirm would
    record - then, after the journal has captured the delete and the row is
    genuinely gone, the row is re-inserted by hand with the SAME id, simulating a
    savepoint that rolled the actual delete back after capture: the journal's own
    entry and the table now disagree, exactly the shape the brief describes.
    """
    from app.services.project_supply_undo_service import UndoJournal, undo_last_confirm

    client, world = api
    db = world.db
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    first = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert first.status_code == 200, first.text
    decision1 = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )
    row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )
    po, po_line = _po_line(db, world, qty=20)
    link, claim = _link_with_claim(db, world, row, po_line, qty=20, so_number=core_so.so_number)
    db.commit()
    link_id = link.id
    link_document = link.document
    claim_id = claim.id

    decision2_id = _uid()
    with UndoJournal(db) as journal:
        decision1.state = "superseded"
        decision1.superseded_at = datetime.utcnow()
        decision1.superseded_reason = "Reconfirmed by CS."
        db.flush()
        decision2 = SOSupplyDecision(
            id=decision2_id, company_id=world.company_id, project_sales_order_id=order.id,
            revision_no=2, state="active",
            line_snapshots=[{"line_no": 10, "project_line_id": line.id}],
            confirmed_by=world.eling, confirmed_at=datetime.utcnow(),
            supersedes_id=decision1.id,
        )
        db.add(decision2)
        db.flush()
        db.delete(link)
        db.flush()
    journal.attach(decision2)
    db.commit()

    assert (
        db.query(OrderInquiryLink).filter(OrderInquiryLink.id == link_id).first() is None
    ), "the delete really ran - the setup this test needs"

    # A savepoint rolled the delete back AFTER the journal already captured it: the
    # row is back in the table with the SAME id, but the journal's own entry still
    # says `delete`.
    db.add(
        OrderInquiryLink(
            id=link_id, company_id=world.company_id, row_id=row.id, po_line_id=po_line.id,
            document=link_document, qty=Decimal("20"), linked_by=world.eling, auto=True,
            claim_id=claim_id,
        )
    )
    db.commit()

    assert (
        db.query(OrderInquiryLink).filter(OrderInquiryLink.id == link_id).count() == 1
    ), "the row survived - the setup this test needs"

    result = undo_last_confirm(db, order, actor_user_id=world.eling)
    assert result["revision_no"] == 2

    assert (
        db.query(OrderInquiryLink).filter(OrderInquiryLink.id == link_id).count() == 1
    ), "replay must not duplicate a row that survived"


def test_parking_an_undo_that_would_be_refused_answers_409_at_park_time(api):
    """New contract, follow-up to AC-UC-41: the CREATE route itself pre-checks the
    refusal predicate before parking a countdown, for an order whose undo
    `_grouped_refusals` already rejects - here the `linked_after` arm from
    `test_purchasing_action_after_the_confirm_refuses_the_undo` above, a link on the
    touched row timestamped after the confirm, absent from the confirm's own insert
    set. Expect a synchronous 409 with the refusal code in the body, and no pending
    row parked at all - the disabled gear entry is the only place a browser click
    ever reaches this, but a raw API call must get the same answer, not a countdown
    that fails a few seconds later (see `documentation/plans/scm/evidence/
    board-undo-last-confirm/AC-UC-41-refused-409.txt` for the measured 202 today).

    Today `create_pending_action` parks unconditionally for any UI-channel caller
    (202) and only refuses at COMMIT time, several seconds after this call returns.
    """
    fixture = _confirm_linked_world(api, second_buy_qty="15")
    client = fixture["client"]
    db = fixture["db"]
    world = fixture["world"]
    order = fixture["order"]
    row = fixture["row"]
    decision2 = fixture["decision2"]

    db.expire_all()
    row = db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row.id).one()
    decision2 = db.query(SOSupplyDecision).filter(SOSupplyDecision.id == decision2.id).one()

    po, po_line = _po_line(db, world, qty=15)
    db.add(
        OrderInquiryLink(
            id=_uid(), company_id=world.company_id, row_id=row.id, po_line_id=po_line.id,
            document=po.po_number, qty=Decimal("5"), linked_by=world.eling, auto=False,
            linked_at=decision2.confirmed_at + timedelta(minutes=1),
        )
    )
    db.commit()

    response = client.post(
        "/api/v1/pending-actions",
        json={
            "action_key": "project_sales_order.undo_confirm",
            "entity_type": "project_sales_order",
            "entity_id": str(order.id),
            "payload": {"decision_id": str(decision2.id)},
        },
    )
    assert response.status_code == 409, response.text
    assert response.json().get("code") == "linked"

    from app.models.sla import SlaFormAction

    assert (
        db.query(SlaFormAction)
        .filter(SlaFormAction.source_entity_id == str(order.id))
        .count()
        == 0
    ), "no pending row was parked for a refusal caught at park time"


# =============================================================================== #
# `PLAN-scm-oi-handover-r2-undo.md` S4 - depth-N undo, post-image `changed` guard  #
# =============================================================================== #


def test_undo_twice_then_refuses(api):
    """AC-R2-22: two journaled revisions undo cleanly back to nothing (the second
    undo replays rev1's OWN journal, which R2-21 keeps alive), and a third undo then
    refuses `no_journal` - there is nothing left to replay."""
    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    order = fixture["order"]

    from app.services.project_supply_undo_service import undo_last_confirm

    first_undo = undo_last_confirm(db, order, actor_user_id=fixture["world"].eling)
    assert first_undo["revision_no"] == 2

    second_undo = undo_last_confirm(db, order, actor_user_id=fixture["world"].eling)
    assert second_undo["revision_no"] == 1

    db.expire_all()
    active = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == "active",
        )
        .first()
    )
    assert active is None, "AC-R2-22: after two undos the order has no active decision"

    with pytest.raises(AppException) as exc_info:
        undo_last_confirm(db, order, actor_user_id=fixture["world"].eling)
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "no_journal"


def test_changed_row_refuses_at_park_and_execute(api):
    """AC-R2-24: a journalled row whose CURRENT value differs from its own journal
    entry's `new` (another writer touched it after the confirm) refuses BOTH at park
    time (409, code "changed") and at undo's own execute time - the replay never runs.
    The baseline (nothing mutated) does not refuse."""
    from sqlalchemy import text as sa_text

    fixture = _confirm_linked_world(api, second_buy_qty="15")
    client = fixture["client"]
    db = fixture["db"]
    order = fixture["order"]
    row = fixture["row"]
    decision2 = fixture["decision2"]

    from app.services.project_supply_undo_service import refusal_for_order, undo_last_confirm

    # Baseline: nothing has touched the row since the confirm - no `changed` refusal.
    assert refusal_for_order(db, order.id) != "changed", (
        "setup: the unmutated row must not refuse before the write below"
    )

    # Another writer changes the SAME journalled column the second confirm's own
    # settle-in-place wrote (`order_inquiry_rows.qty`), bypassing the ORM so it is
    # genuinely "since the confirm", not part of this journal. UNQUALIFIED table
    # name (review round, captain's diagnosis): `blank_session()` routes raw SQL
    # through `search_path`, never `schema_translate_map` - a schema-qualified
    # `projects.order_inquiry_rows` resolves against the REAL `projects` schema
    # outside the scratch one, so the UPDATE lands on zero rows there and this
    # test's own mutation never happens.
    db.execute(
        sa_text("UPDATE order_inquiry_rows SET qty = :qty WHERE id = :id"),
        {"qty": Decimal("999"), "id": str(row.id)},
    )
    db.commit()

    refusal = refusal_for_order(db, order.id)
    assert refusal == "changed", f"AC-R2-24: expected 'changed', got {refusal!r}"

    park = client.post(
        "/api/v1/pending-actions",
        json={
            "action_key": "project_sales_order.undo_confirm",
            "entity_type": "project_sales_order",
            "entity_id": str(order.id),
            "payload": {"decision_id": str(decision2.id), "mode": "journal"},
        },
    )
    assert park.status_code == 409, park.text
    assert park.json().get("code") == "changed"

    before = _snapshot(db)
    with pytest.raises(AppException) as exc_info:
        undo_last_confirm(db, order, actor_user_id=fixture["world"].eling)
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "changed"
    assert _snapshot(db) == before, "a refused undo must replay nothing"


def test_legacy_entry_without_new_is_skipped(api):
    """AC-R2-25: a legacy journal entry (written before this lane, so it carries no
    `new` key at all) is exempt from the `changed` check - undo proceeds even though
    the row's live value no longer matches what the entry's `old` recorded."""
    from sqlalchemy import text as sa_text

    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    order = fixture["order"]
    row = fixture["row"]
    decision2 = fixture["decision2"]

    db.expire_all()
    decision2 = db.query(SOSupplyDecision).filter(SOSupplyDecision.id == decision2.id).one()
    journal = decision2.undo_journal
    assert journal, "setup: the second confirm must carry its own journal"
    stripped = [{k: v for k, v in entry.items() if k != "new"} for entry in journal]

    table = SOSupplyDecision.__table__
    db.execute(table.update().where(table.c.id == decision2.id).values(undo_journal=stripped))
    # Another writer touches the SAME journalled column - if the changed check were
    # NOT skipped for a `new`-less entry, this alone would refuse. UNQUALIFIED table
    # name, same routing fix as `test_changed_row_refuses_at_park_and_execute` above.
    db.execute(
        sa_text("UPDATE order_inquiry_rows SET qty = :qty WHERE id = :id"),
        {"qty": Decimal("999"), "id": str(row.id)},
    )
    db.commit()

    from app.services.project_supply_undo_service import refusal_for_order, undo_last_confirm

    assert refusal_for_order(db, order.id) != "changed", (
        "AC-R2-25: a legacy entry with no `new` must be exempt from the changed check"
    )

    result = undo_last_confirm(db, order, actor_user_id=fixture["world"].eling)
    assert result["revision_no"] == 2


def test_second_undo_passes_changed_check(api):
    """AC-R2-26: after undoing rev2 (which restores rev1's own post-image on the
    touched row from rev2's journal), undoing rev1 in turn must NOT refuse `changed` -
    rev2's own `old` values are rev1's `new` values by construction, so the two agree."""
    fixture = _confirm_linked_world(api, second_buy_qty="15")
    db = fixture["db"]
    order = fixture["order"]

    from app.services.project_supply_undo_service import refusal_for_order, undo_last_confirm

    undo_last_confirm(db, order, actor_user_id=fixture["world"].eling)
    db.expire_all()

    refusal = refusal_for_order(db, order.id)
    assert refusal != "changed", (
        f"AC-R2-26: the reinstated revision's own changed check must pass, got {refusal!r}"
    )

    result = undo_last_confirm(db, order, actor_user_id=fixture["world"].eling)
    assert result["revision_no"] == 1


def test_board_undo_carries_mode_and_changed():
    """AC-R2-27: `BoardUndo` gains `mode` (always present once `undo` is present) and
    `refusal` accepts the new `"changed"` member. Pinned at the schema directly - the
    board READ itself deliberately does NOT compute `changed` (S4's own note: that runs
    at park/execute time only); `board_undo_map`'s own `mode` key is exercised by
    `test_undo_keeps_reinstated_journal` and `test_journalless_decision_refuses_non_
    admin_offers_reconstructed_to_admin` above."""
    from app.schemas.project_board import BoardUndo

    undo = BoardUndo(revision_no=1, refusal="changed", decision_id="dec-1", mode="journal")
    assert undo.mode == "journal", "AC-R2-27: BoardUndo must carry `mode`"
    assert undo.refusal == "changed", (
        "AC-R2-27: `refusal` must accept the new 'changed' member"
    )

    reconstructed = BoardUndo(
        revision_no=2, refusal=None, decision_id="dec-2", mode="reconstructed"
    )
    assert reconstructed.mode == "reconstructed"

