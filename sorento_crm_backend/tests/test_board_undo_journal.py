"""S1 (#978) - the undo journal a board Confirm writes, and the board's `undo` field.

RED for Phase 2 of `documentation/plans/scm/PLAN-board-undo-last-confirm.md`. Nothing in
this file has an implementation yet:

* `projects.so_supply_decisions.undo_journal` (migration `undo_0001`) does not exist on the
  model, so every test that reads `decision.undo_journal` fails with an `AttributeError` -
  that is this file's "right reason" until the coder adds the column.
* `BoardOrderStanding.undo` (the board payload field) does not exist on the response schema,
  so a test reading `order["undo"]` off `GET .../fulfilment-planning/board` fails with a
  `KeyError` - `response_model` silently drops an undeclared field (see this repo's own
  lesson on that), so the field has to be declared on BOTH the schema and the service dict
  before it ever reaches the wire.

Postgres only, via `tests/_pg_fixture.py::blank_session` (through the `api` fixture this file
imports) except for the two batch-fork tests, which reuse
`tests/test_planning_change_apply_on_board.py`'s own `world`/`api` fixtures running on the
REAL local database (rolled back) because `scm.committed_v` is a view the blank scratch
schema does not carry. Every row is seeded behind the `ZZT` marker; nothing is borrowed from
an existing table.

Contract pinned here (Phase 1 contract doc, `boardUndoMock.ts`; PLAN "The design"):

    undo_journal: JSON list of {"seq": int, "op": "insert"|"update"|"delete",
                                 "table": "<schema>.<table>", "pk": "<id as string>",
                                 "old": {column: value} | null}

    board order.undo: null | {"revision_no": int, "confirmed_at": iso,
                               "confirmed_by_name": str,
                               "refusal": null | "linked" | "actioned"}

Review round (amended after S1/S2/S3 landed - reviewer + security-reviewer finding): the
refusal code `manual_link` is renamed `linked` and its predicate is no longer a raw
`auto=False AND linked_at > confirmed_at` timestamp comparison - `confirmed_at` is a
PYTHON `datetime.utcnow()` captured once near the top of `_write_decision`, while
`linked_at` is POSTGRES `now()` resolved at the link row's own INSERT, later in the same
request; the two clocks are not the same instant, so a step-3 borrow's own `auto=False`
link written milliseconds later in the SAME transaction could read `linked_at >
confirmed_at` too and be wrongly refused. `linked` instead asks whether the link's own id
is in THIS confirm's own journal insert set for `projects.order_inquiry_links` - if the
confirm itself wrote it, it is not purchasing acting after the fact, whatever the clocks
say.
"""
from __future__ import annotations

import pytest
from datetime import date, timedelta
from decimal import Decimal

from app.models.audit import AuditLog
from app.models.project_so import (
    INQUIRY_PLACED,
    IV_ORDER,
    OrderInquiryLink,
    OrderInquiryRow,
    SOSupplyDecision,
    SOSupplyDecisionDraft,
)
from app.models.projects import ProjectCollaborator
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
    _stock,
    _suffix,
    _uid,
    _user,
    _warehouse,
    api,
)

MARKER = "zzt-undo-journal"


# --------------------------------------------------------------------------- AC-UC-10


def test_a_plain_confirm_journals_every_row_it_touched(api):
    """AC-UC-10: one plain Confirm's journal names every row the transaction touched.

    Two confirms build the world: the FIRST raises line1's Buy and (manually, standing in
    for the raise-time auto-place cascade this file does not exercise) links it, so the
    SECOND confirm - the one whose journal is read - both fully covers line1 from stock
    (the zero-need settle that removes that link) and freshly Buys line2 (a brand new
    raised row). Two drafts are saved between the two confirms so the second one's own
    `delete_drafts_for_lines` clears them and the journal has to say so.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=200)
    core_so = _core_so(db, world.company_id)
    core_line_1 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    core_line_2 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    order = _project_so(db, world.project, so_id=core_so.id)
    line1 = _project_line(db, order, line_no=10, product=world.product, core_line=core_line_1)
    line2 = _project_line(db, order, line_no=20, product=world.product, core_line=core_line_2)
    db.commit()

    first = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line1.id, buy_qty="20")]},
    )
    assert first.status_code == 200, first.text

    decision1 = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )
    row1 = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line1.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )

    from app.models.procurement import PurchaseOrder, PurchaseOrderLine

    po = PurchaseOrder(
        id=_uid(), company_id=world.company_id, po_number=f"ZZT-PO-{_suffix()}",
        issue_date=date.today(), status="active",
    )
    db.add(po)
    db.flush()
    po_line = PurchaseOrderLine(
        id=_uid(), company_id=world.company_id, purchase_order_id=po.id,
        product_id=world.product.id, warehouse_id=world.own_wh.id,
        qty_ordered=Decimal("20"), qty_received=Decimal("0"),
        expected_date=date.today(), line_status="open",
    )
    db.add(po_line)
    db.flush()
    link = OrderInquiryLink(
        id=_uid(), company_id=world.company_id, row_id=row1.id, po_line_id=po_line.id,
        document=po.po_number, qty=Decimal("20"), linked_by=world.eling, auto=True,
    )
    db.add(link)
    row1.state = INQUIRY_PLACED
    db.commit()

    key1 = f"{core_so.id}|10|{world.product.product_code}|bkt"
    key2 = f"{core_so.id}|20|{world.product.product_code}|bkt"
    # R1 (`PLAN-board-draft-on-confirmed-line.md`): line1 is already covered by `decision1`
    # from the first confirm above, so its draft's verdict has to be `amended` - the only one
    # a covered line's save still accepts. The decision content is otherwise opaque to
    # `save_draft` and unread by every assertion below.
    project_line_draft_service.save_draft(
        db, key1, decision={"x": 1, "verdict": "amended"}, actor_user_id=world.eling
    )
    project_line_draft_service.save_draft(db, key2, decision={"x": 2}, actor_user_id=world.eling)
    db.commit()
    draft_ids = {
        str(row.id)
        for row in db.query(SOSupplyDecisionDraft)
        .filter(SOSupplyDecisionDraft.core_line_id.in_([core_line_1.id, core_line_2.id]))
        .all()
    }
    assert len(draft_ids) == 2, "both drafts must exist before the second confirm clears them"

    second = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line1.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "20"}]
                ),
                _line_payload(line2.id, buy_qty="10"),
            ]
        },
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
    journal = decision2.undo_journal
    assert journal, "the confirming revision must carry its own journal"

    inserts = [e for e in journal if e["op"] == "insert"]
    updates = [e for e in journal if e["op"] == "update"]
    deletes = [e for e in journal if e["op"] == "delete"]

    assert any(
        e["table"].endswith("so_supply_decisions") and e["pk"] == str(decision2.id)
        for e in inserts
    ), "the new decision itself is in its own journal"
    assert any(
        e["table"].endswith("so_line_allocations") for e in inserts
    ), "the new decision's allocations are journalled"
    assert any(
        e["table"].endswith("order_inquiry_rows") for e in inserts
    ), "line2's freshly raised row is journalled"

    sup = next(
        e for e in updates
        if e["table"].endswith("so_supply_decisions") and e["pk"] == str(decision1.id)
    )
    assert sup["old"]["state"] == "active"
    assert sup["old"]["superseded_at"] is None
    assert sup["old"]["superseded_reason"] is None

    deleted_draft_ids = {
        e["pk"] for e in deletes if e["table"].endswith("so_supply_decision_drafts")
    }
    assert deleted_draft_ids == draft_ids, "both drafts the confirm cleared are journalled"

    link_delete = next(
        (e for e in deletes if e["table"].endswith("order_inquiry_links") and e["pk"] == str(link.id)),
        None,
    )
    assert link_delete is not None, "the link the zero-need settle removed is journalled"
    assert link_delete["old"]["qty"] in ("20", "20.0000", 20)
    assert link_delete["old"]["po_line_id"] == str(po_line.id)
    assert link_delete["old"]["row_id"] == str(row1.id)


# --------------------------------------------------------------------------- AC-UC-11


def test_a_batch_confirm_journals_the_batch_rows_and_header():
    """AC-UC-11: the batch fork's own writes (`planning_change_rows`,
    `planning_change_batches`) are in the journal beside the ordinary confirm writes.

    Reuses `tests/test_planning_change_apply_on_board.py`'s own seeding wholesale - it is
    the one file that already builds a re-uploaded-book batch against the real (migrated)
    database, which `scm.committed_v` requires. Its `world`/`api` are pytest FIXTURES
    (registered under those exact names), so importing them into this module would collide
    with the `api` fixture this file already imports from `test_so_supply_confirmation`;
    their bodies are replicated inline instead of importing the fixtures themselves.
    """
    from app.models.base import company_scope
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
            title=f"{BATCH_MARKER} Undo Batch World",
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

                decision = (
                    db.query(SOSupplyDecision)
                    .filter(SOSupplyDecision.project_sales_order_id == fixture["order"].id)
                    .order_by(SOSupplyDecision.revision_no.desc())
                    .first()
                )
                journal = decision.undo_journal
                assert journal, "the batch-fork confirm must carry its own journal too"

                updates = [e for e in journal if e["op"] == "update"]
                batch_row_update = next(
                    (e for e in updates if e["table"].endswith("planning_change_rows")), None
                )
                assert batch_row_update is not None
                assert batch_row_update["old"]["applied_state"] == "pending"

                batch_update = next(
                    (e for e in updates if e["table"].endswith("planning_change_batches")), None
                )
                assert batch_update is not None
                assert batch_update["old"]["applied_at"] is None
        finally:
            batch_restore(originals)


# --------------------------------------------------------------------------- AC-UC-12


def test_a_borrow_from_a_donor_journals_the_donor_re_issue(api):
    """AC-UC-12: a cross-project borrow's donor re-issue (`_reissue_without_line`) lands
    in the BORROWER's own journal, since it is the borrower's transaction that writes it.

    The donor must cover at least two lines, or `_reissue_without_line` has nothing left
    to re-issue once the borrowed line is dropped and only supersedes - a re-issue insert
    that would never happen.
    """
    from app.services.project_service import register_project

    client, world = api
    db = world.db
    borrow_from_order_on(db)
    donor_wh = _warehouse(db, f"ZZT-DNR2-{_suffix()}")
    _stock(db, world.product, donor_wh, on_hand=100)
    # Owned by the SAME actor as `world.project` (`world.eling`, the confirming client), not
    # a second user: `register_project`'s own salesperson check would otherwise refuse the
    # donor's own confirm below with `project_not_editable` before the donor decision this
    # test needs even exists.
    donor_project = register_project(
        db, company_id=world.company_id, actor_user_id=world.eling, developer_party_id=None,
        title=f"{MARKER} Donor Heights",
    )
    donor_core_so = _core_so(db, world.company_id)
    donor_core_line_1 = _core_line(db, donor_core_so, world.product, donor_wh, qty_ordered="40")
    donor_core_line_2 = _core_line(db, donor_core_so, world.product, donor_wh, qty_ordered="20")
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
                _line_payload(
                    donor_line_2.id, reserve=[{"warehouse_id": donor_wh.id, "qty": "20"}]
                ),
            ]
        },
    )
    assert donor_confirm.status_code == 200, donor_confirm.text
    donor_decision_1 = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == donor_pso.id)
        .one()
    )

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
                            "reason": "Donor Heights lending surplus.",
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
    journal = borrower_decision.undo_journal
    assert journal, "the borrower's own confirm carries the donor's re-issue too"

    db.refresh(donor_decision_1)
    assert donor_decision_1.state == "superseded"
    donor_decision_2 = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == donor_pso.id,
            SOSupplyDecision.state == "active",
        )
        .one()
    )
    assert donor_decision_2.supersedes_id == donor_decision_1.id

    updates = [e for e in journal if e["op"] == "update"]
    inserts = [e for e in journal if e["op"] == "insert"]
    assert any(
        e["table"].endswith("so_supply_decisions") and e["pk"] == str(donor_decision_1.id)
        and e["old"]["state"] == "active"
        for e in updates
    ), "the donor's superseded decision is in the BORROWER's journal"
    assert any(
        e["table"].endswith("so_supply_decisions") and e["pk"] == str(donor_decision_2.id)
        for e in inserts
    ), "the donor's re-issued decision is in the BORROWER's journal"


# --------------------------------------------------------------------------- AC-UC-13


def test_confirm_all_journals_each_order_separately_and_a_refused_order_writes_nothing(api):
    """AC-UC-13: `confirm-all` runs one journal per order (`confirm_many` is one savepoint
    per order); the second order's refusal writes no decision and therefore no journal."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=300)

    orders = []
    for _ in range(3):
        core_so = _core_so(db, world.company_id)
        core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
        order = _project_so(db, world.project)
        line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
        orders.append((order, line))
    db.commit()

    payload = {
        "orders": [
            {
                "pso_id": orders[0][0].id,
                "lines": [
                    _line_payload(
                        orders[0][1].id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "20"}]
                    )
                ],
            },
            {
                # unbalanced: buy 10 against an open qty of 20 - refused whole (AC-C02 shape).
                "pso_id": orders[1][0].id,
                "lines": [_line_payload(orders[1][1].id, buy_qty="10")],
            },
            {
                "pso_id": orders[2][0].id,
                "lines": [
                    _line_payload(
                        orders[2][1].id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "20"}]
                    )
                ],
            },
        ]
    }
    response = client.post(f"{BASE}/fulfilment-planning/confirm-all", json=payload)
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
    assert results[2]["ok"] is True

    dec0 = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == orders[0][0].id)
        .one()
    )
    dec2 = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == orders[2][0].id)
        .one()
    )
    assert dec0.undo_journal, "order 1's own journal"
    assert dec2.undo_journal, "order 3's own journal"
    assert dec0.undo_journal != dec2.undo_journal, "each order's journal is its own"
    assert (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == orders[1][0].id)
        .count()
        == 0
    ), "the refused order wrote no decision at all"


# --------------------------------------------------------------------------- AC-UC-14


def test_a_rejection_re_issue_has_no_journal(api):
    """AC-UC-14: `uncover_lines`, minted by a purchasing rejection, is not one of the two
    board routes - its revision carries no journal at all.

    A SECOND line survives the rejection deliberately: `uncover_lines` only mints a fresh
    revision when something remains to cover (`PSS:6146`) - a rejected line that was the
    ONLY one just retires the decision outright with no re-issue at all.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    core_line_2 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    line_2 = _project_line(db, order, line_no=20, product=world.product, core_line=core_line_2)
    db.commit()

    confirm = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(line.id, buy_qty="20"),
                _line_payload(
                    line_2.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}]
                ),
            ]
        },
    )
    assert confirm.status_code == 200, confirm.text

    row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )

    reject = client.post(
        f"{BASE}/order-inquiries/{row.id}/reject", json={"reason": "Factory closed"}
    )
    assert reject.status_code == 200, reject.text

    decisions = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .order_by(SOSupplyDecision.revision_no.desc())
        .all()
    )
    assert len(decisions) == 2, "the rejection un-decides the line through its own revision"
    newest = decisions[0]
    assert newest.revision_no == 2
    assert newest.state == "active"
    assert newest.undo_journal is None, "uncover_lines never journals - only the board routes do"


# --------------------------------------------------------------------------- AC-UC-15


def test_the_board_names_the_undoable_revision_and_its_refusal(api):
    """AC-UC-15: the board payload's `undo` field, one of three shapes per order:
    a journal with no refusal, a journal refused by a manual link written after the
    confirm, and no journal at all (an order nobody has confirmed).
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=300)

    # Order 1: undoable, no refusal.
    core_so_1 = _core_so(db, world.company_id)
    core_line_1 = _core_line(db, core_so_1, world.product, world.own_wh, qty_ordered="10")
    order_1 = _project_so(db, world.project, so_id=core_so_1.id)
    line_1 = _project_line(db, order_1, line_no=10, product=world.product, core_line=core_line_1)

    # Order 2: undoable, but refused by a manual link written after the confirm.
    core_so_2 = _core_so(db, world.company_id)
    core_line_2 = _core_line(db, core_so_2, world.product, world.own_wh, qty_ordered="10")
    order_2 = _project_so(db, world.project, so_id=core_so_2.id)
    line_2 = _project_line(db, order_2, line_no=10, product=world.product, core_line=core_line_2)

    # Order 3: nothing to undo - never confirmed.
    core_so_3 = _core_so(db, world.company_id)
    core_line_3 = _core_line(db, core_so_3, world.product, world.own_wh, qty_ordered="10")
    order_3 = _project_so(db, world.project, so_id=core_so_3.id)
    _project_line(db, order_3, line_no=10, product=world.product, core_line=core_line_3)
    db.commit()

    r1 = client.post(
        f"{BASE}/sales-orders/{order_1.id}/confirm",
        json={"lines": [_line_payload(line_1.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}])]},
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        f"{BASE}/sales-orders/{order_2.id}/confirm",
        json={"lines": [_line_payload(line_2.id, buy_qty="10")]},
    )
    assert r2.status_code == 200, r2.text

    decision_2 = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order_2.id)
        .one()
    )
    row_2 = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line_2.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )
    from app.models.procurement import PurchaseOrder, PurchaseOrderLine

    po = PurchaseOrder(
        id=_uid(), company_id=world.company_id, po_number=f"ZZT-PO-{_suffix()}",
        issue_date=date.today(), status="active",
    )
    db.add(po)
    db.flush()
    po_line = PurchaseOrderLine(
        id=_uid(), company_id=world.company_id, purchase_order_id=po.id,
        product_id=world.product.id, warehouse_id=world.own_wh.id,
        qty_ordered=Decimal("10"), qty_received=Decimal("0"),
        expected_date=date.today(), line_status="open",
    )
    db.add(po_line)
    db.flush()
    manual_link = OrderInquiryLink(
        id=_uid(), company_id=world.company_id, row_id=row_2.id, po_line_id=po_line.id,
        document=po.po_number, qty=Decimal("10"), linked_by=world.eling, auto=False,
        linked_at=decision_2.confirmed_at + timedelta(minutes=1),
    )
    db.add(manual_link)
    db.commit()

    numbers = ",".join([core_so_1.so_number, core_so_2.so_number, core_so_3.so_number])
    board = client.get(f"{BASE}/fulfilment-planning/board", params={"orders": numbers})
    assert board.status_code == 200, board.text
    by_so = {row["so_number"]: row for row in board.json()["orders"]}

    undo_1 = by_so[core_so_1.so_number]["undo"]
    assert undo_1 is not None
    assert undo_1["revision_no"] == 1
    assert undo_1["refusal"] is None

    undo_2 = by_so[core_so_2.so_number]["undo"]
    assert undo_2 is not None
    assert undo_2["revision_no"] == 1
    assert undo_2["refusal"] == "linked"

    assert by_so[core_so_3.so_number]["undo"] is None


# --------------------------------------------------------------------------- review round: composite primary key (contract F)


def test_the_journal_refuses_a_composite_primary_key_table(api):
    """Contract F (review round): `UndoJournal` must raise at capture rather than
    silently join a composite primary key into one joined string - replay's own
    `_pk_column` reads only the FIRST primary key column, so a captured composite-pk
    row would replay a delete/insert/update against the wrong half of its own identity.

    `projects.collaborators` (`ProjectCollaborator`, primary key `(project_id,
    user_id)`) is one of the nine join tables `Base.metadata` carries with more than
    one primary key column.
    """
    from app.services.project_supply_undo_service import UndoJournal

    client, world = api
    db = world.db
    collaborator = ProjectCollaborator(
        project_id=world.project.id, user_id=world.eling, granted_by=world.eling,
    )
    with pytest.raises(Exception):
        with UndoJournal(db):
            db.add(collaborator)
            db.flush()


# --------------------------------------------------------------------------- review round: attaching a journal clears the superseded one (contract H)


def test_attach_keeps_superseded_journal(api):
    """AC-R2-20 (`PLAN-scm-oi-handover-r2-undo.md` S4, rewrite of the old Contract H
    "clears" expectation - depth-N undo needs EVERY revision's own journal to survive
    for life, so a second undo can replay revision 1 the same way the first replayed
    revision 2). Confirming a NEW revision must LEAVE the superseded decision's own
    `undo_journal` exactly as it was, not null it."""
    client, world = api
    db = world.db
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
    assert decision1.undo_journal, "revision 1 must carry its own journal once confirmed"
    decision1_id = decision1.id
    decision1_journal_len = len(decision1.undo_journal)

    second = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert second.status_code == 200, second.text

    db.expire_all()
    decision1 = db.query(SOSupplyDecision).filter(SOSupplyDecision.id == decision1_id).one()
    decision2 = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.revision_no == 2,
        )
        .one()
    )
    assert decision1.undo_journal, (
        "AC-R2-20: the superseded decision's own journal must be KEPT at confirm time, "
        "not cleared - a later undo of revision 2 has to be able to replay revision 1's "
        "own journal in turn"
    )
    assert len(decision1.undo_journal) == decision1_journal_len, (
        "the kept journal must be exactly what revision 1's own confirm wrote"
    )
    assert decision2.undo_journal, "the new active decision carries its own journal"


# --------------------------------------------------------------------------- review round: refused confirm leaves no journal and no flush error (contract J)


def test_a_refused_confirm_leaves_no_journal_and_no_flush_error(api):
    """Contract J (review round): `UndoJournal.__exit__` must not flush when the
    wrapped block raised - the route wraps the WHOLE confirm call in `UndoJournal(db):`,
    and a business-logic refusal leaves the session mid-transaction; flushing anyway in
    `__exit__`'s own `finally` clause can itself raise and turn a clean 409 into a 500.

    Precedent: `tests/test_so_supply_confirmation.py:630`
    (`test_a_second_confirmation_racing_an_already_active_decision_gets_a_conflict_with_no_partial_writes`),
    driven the same way - patch `active_decision` to answer `None` while a winner is
    already committed, so the loser's insert collides with the real partial unique
    index.
    """
    from app.services.project_supply_service import ProjectSupplyService

    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=50)
    order = _project_so(db, world.project, so_id=None)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    winner = SOSupplyDecision(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        revision_no=1, state="active",
        line_snapshots=[{"line_no": 10, "project_line_id": line.id}],
        confirmed_by=world.eling, confirmed_at=None,
    )
    db.add(winner)
    db.commit()

    original = ProjectSupplyService.active_decision
    try:
        ProjectSupplyService.active_decision = lambda self, pso_id: None
        response = client.post(
            f"{BASE}/sales-orders/{order.id}/confirm",
            json={
                "lines": [
                    _line_payload(
                        line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "50"}]
                    )
                ]
            },
        )
    finally:
        ProjectSupplyService.active_decision = original

    assert response.status_code == 409, response.text

    remaining = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .all()
    )
    assert len(remaining) == 1, "the loser must not have written a second decision"
    assert remaining[0].id == winner.id
    assert remaining[0].undo_journal is None, (
        "the loser never got far enough to attach a journal to anything"
    )


# --------------------------------------------------------------------------- AC-R2-23 (S4)


def test_update_entry_carries_new(api):
    """AC-R2-23 (`PLAN-scm-oi-handover-r2-undo.md` S4): an `update` journal entry also
    records `new` - the values written for the SAME keys as `old` - so a later `changed`
    check (AC-R2-24) can compare a journalled row's CURRENT value against what this
    confirm's own write actually left there, not merely what it found."""
    from app.services.project_supply_undo_service import UndoJournal, _table_name

    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    order = _project_so(db, world.project, so_id=core_so.id)
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()
    before_qty = str(line.qty)

    with UndoJournal(db) as journal:
        line.qty = Decimal("25")
        db.flush()

    table = _table_name(line)
    entries = [
        e for e in journal.entries
        if e["op"] == "update" and e["table"] == table and e["pk"] == str(line.id)
    ]
    assert entries, f"expected an update entry for the line, got {journal.entries}"
    entry = entries[0]
    assert entry["old"].get("qty") == before_qty
    assert entry.get("new") is not None, (
        f"AC-R2-23: an update entry must carry `new`, got {entry}"
    )
    assert entry["new"].get("qty") == "25", (
        f"AC-R2-23: `new` must hold the values actually written, got {entry['new']}"
    )
