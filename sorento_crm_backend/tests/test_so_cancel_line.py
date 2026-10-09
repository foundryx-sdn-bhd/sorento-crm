"""SO-CANCEL-LINE: Transfer to Cancel SO on a board line (backend, Phase 2 red tests).

Contract: `documentation/plans/scm/PLAN-so-cancel-line.md` and
`documentation/plans/scm/so-cancel-line-acceptance-criteria.md` (AC-C1 to AC-C13).

TEST-FIRST: written before any code. `ConfirmSupplyBody.cancelled_lines`, the three
`sales_order_lines` columns (`cancelled_at`, `cancelled_by_id`, `cancel_reason`) and the
ingest guard do not exist, so each test fails on a missing key / column / 422-from-pydantic
(an unknown body field is ignored today, so the cancel simply does not happen), never on a
fixture typo.

Fixture shape = the owner's case with fake ids: core lines ordered 36 / delivered 36 /
`closed`, an acknowledged ORDER inquiry row of 36 on one of them, another open line beside.
Postgres only (`blank_session` via the shared `api` fixture); every test seeds its own chain.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.order import SalesOrderLine
from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    ACK_AWAITING,
    ACK_CHANGED,
    DECISION_ACTIVE,
    INQUIRY_CANCELLED,
    INQUIRY_RAISED,
    SOSupplyDecision,
    SOSupplyDecisionDraft,
)

from .test_fulfilment_line_draft_route import (  # noqa: F401 - `api` is a fixture
    _board,
    _covered_two_line_world,
)
from .test_oi_cancelled_line_used_confirm import _inquiry, _oi_row
from .test_so_supply_confirmation import (  # noqa: F401
    BASE,
    EDIT,
    _core_line,
    _core_so,
    _line_payload,
    _product,
    _project_line,
    _project_so,
    _stock,
    api,
)

REASON = "ZZT customer cancelled these on the phone"


def _cb_world(world):
    """Order with CB110 / CB110-R (closed 36/36), plus an open Buy line. Returns a dict."""
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=1000)
    core_so = _core_so(db, world.company_id)
    order = _project_so(db, world.project, so_id=core_so.id)
    cancelled = []
    for line_no in (10, 20):
        product = _product(db)
        core = _core_line(
            db, core_so, product, world.own_wh, qty_ordered="36", qty_delivered="36"
        )
        core.line_status = "closed"
        mirror = _project_line(db, order, line_no=line_no, product=product, core_line=core)
        cancelled.append((core, mirror, product))
    open_core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="5")
    open_line = _project_line(db, order, line_no=30, product=world.product, core_line=open_core)
    db.commit()
    return {
        "core_so": core_so,
        "order": order,
        "a": cancelled[0],
        "b": cancelled[1],
        "open": (open_core, open_line),
    }


def _cancel_body(*mirrors, reason=REASON, lines=None):
    return {
        "lines": lines or [],
        "cancelled_lines": [{"project_line_id": m.id, "reason": reason} for m in mirrors],
    }


def _confirm_all(client, order, **body):
    response = client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={"orders": [dict(body, pso_id=order.id)]},
    )
    assert response.status_code == 200, response.text
    return response.json()["results"][0]


def _core(db, core_line_id):
    db.expire_all()
    return db.get(SalesOrderLine, str(core_line_id))


def _raw(db, core_line_id):
    return db.execute(
        text(
            "SELECT line_status, cancelled_at, cancelled_by_id, cancel_reason "
            "FROM sales_order_lines WHERE id = :i"
        ),
        {"i": str(core_line_id)},
    ).one()


def _status(db, core_line_id):
    db.expire_all()
    return db.execute(
        text("SELECT line_status FROM sales_order_lines WHERE id = :i"),
        {"i": str(core_line_id)},
    ).scalar()


def test_sanity_fixture_chain_builds_and_plain_confirm_works(api):
    """Not an AC: proves the chain seeds and the existing confirm route accepts it."""
    client, world = api
    w = _cb_world(world)
    open_core, open_line = w["open"]
    response = client.post(
        f"{BASE}/sales-orders/{w['order'].id}/confirm",
        json={"lines": [_line_payload(open_line.id, buy_qty="5")]},
    )
    assert response.status_code == 200, response.text
    status = world.db.execute(
        text("SELECT line_status FROM sales_order_lines WHERE id = :i"),
        {"i": str(w["a"][0].id)},
    ).scalar()
    assert status == "closed"


# ---------------------------------------------------------------- AC-C1, C6, C7


def test_c1_confirm_all_cancels_the_line_and_echoes_it(api):
    """AC-C1."""
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]

    result = _confirm_all(client, w["order"], **_cancel_body(mirror_a))

    assert result["ok"] is True, result
    assert result["lines_cancelled"] == [
        {"line_no": 10, "item_code": product_a.product_code}
    ]
    row = _raw(world.db, core_a.id)
    assert row.line_status == "cancelled"
    assert row.cancelled_at is not None
    assert str(row.cancelled_by_id) == str(world.eling)
    assert row.cancel_reason == REASON
    # the sibling closed line was not named: untouched
    assert _raw(world.db, w["b"][0].id).line_status == "closed"


def test_c1_single_confirm_route_also_cancels(api):
    """AC-C1 on `POST .../sales-orders/{pso_id}/confirm` (same body)."""
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]

    response = client.post(
        f"{BASE}/sales-orders/{w['order'].id}/confirm", json=_cancel_body(mirror_a)
    )

    assert response.status_code == 200, response.text
    assert response.json()["lines_cancelled"] == [
        {"line_no": 10, "item_code": product_a.product_code}
    ]
    assert _raw(world.db, core_a.id).line_status == "cancelled"


def test_c6_the_saved_draft_of_a_cancelled_line_is_deleted(api):
    """AC-C6: only that line's draft goes; the other line's draft stays."""
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    core_b, _mirror_b, product_b = w["b"]
    db = world.db
    for core, product, line_no in ((core_a, product_a, 10), (core_b, product_b, 20)):
        db.add(
            SOSupplyDecisionDraft(
                sales_order_id=w["core_so"].id,
                core_line_id=core.id,
                line_no=line_no,
                item_code=product.product_code,
                bucket_key="2026-W41",
                decision={"verdict": "cancelled", "reason": REASON},
                saved_by=world.eling,
            )
        )
    db.commit()

    _confirm_all(client, w["order"], **_cancel_body(mirror_a))

    db.expire_all()
    left = {
        str(d.core_line_id)
        for d in db.query(SOSupplyDecisionDraft).filter(
            SOSupplyDecisionDraft.sales_order_id == w["core_so"].id
        )
    }
    assert left == {str(core_b.id)}


def test_c7_acknowledged_inquiry_rows_are_cancelled_others_untouched(api):
    """AC-C7, updated by SO-CANCEL-HANDOVER AC-H14: the acknowledged row on the cancelled
    line is now cancelled (handed over), not flipped to changed."""
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    core_b, mirror_b, product_b = w["b"]
    db = world.db
    inquiry = _inquiry(db, world.company_id, w["order"].id)
    ack_a = _oi_row(db, world.company_id, inquiry.id, mirror_a.id, product_a.product_code, "36")
    awaiting_a = _oi_row(
        db, world.company_id, inquiry.id, mirror_a.id, product_a.product_code, "6",
        ack_state=ACK_AWAITING,
    )
    ack_b = _oi_row(db, world.company_id, inquiry.id, mirror_b.id, product_b.product_code, "36")
    db.commit()

    _confirm_all(client, w["order"], **_cancel_body(mirror_a))

    db.expire_all()
    for row in (ack_a, awaiting_a, ack_b):
        db.refresh(row)
    assert ack_a.state == INQUIRY_CANCELLED
    assert ack_a.ack_state != ACK_CHANGED
    assert ack_a.changed_at is None
    assert awaiting_a.state == INQUIRY_CANCELLED
    assert ack_b.state == INQUIRY_RAISED
    assert ack_b.ack_state == ACK_ACKNOWLEDGED
    assert ack_b.changed_at is None


# ---------------------------------------------------------------- AC-C2..C5


def test_c2_denied_without_the_edit_permission_and_nothing_changes(api):
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, _p = w["a"]
    from app.services.user_service import UserPermissionService

    original = UserPermissionService.check_user_has_permission
    try:
        UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug != EDIT
        response = client.post(
            f"{BASE}/sales-orders/{w['order'].id}/confirm",
            json=_cancel_body(
                mirror_a, lines=[_line_payload(w["open"][1].id, buy_qty="5")]
            ),
        )
    finally:
        UserPermissionService.check_user_has_permission = original
    assert response.status_code == 403, response.text
    assert _status(world.db, core_a.id) == "closed"


@pytest.mark.parametrize("reason", ["", "   "])
def test_c3_blank_reason_is_422_and_nothing_changes(api, reason):
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, _p = w["a"]
    response = client.post(
        f"{BASE}/sales-orders/{w['order'].id}/confirm",
        json=_cancel_body(
            mirror_a, reason=reason, lines=[_line_payload(w["open"][1].id, buy_qty="5")]
        ),
    )
    assert response.status_code == 422, response.text
    assert "nothing was chosen" not in response.text.lower()
    assert _status(world.db, core_a.id) == "closed"


def test_c4_a_line_covered_by_an_active_decision_is_409_reject_first(api):
    client, world, core_so, order, core_1, line_1, _core_2, _line_2 = (
        _covered_two_line_world(api)
    )
    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm", json=_cancel_body(line_1)
    )
    assert response.status_code == 409, response.text
    assert "reject" in response.text.lower()
    assert _raw(world.db, core_1.id).line_status != "cancelled"
    active = (
        world.db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == DECISION_ACTIVE,
        )
        .count()
    )
    assert active == 1


def test_c5_an_already_cancelled_line_is_409_and_not_rewritten(api):
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, _p = w["a"]
    url = f"{BASE}/sales-orders/{w['order'].id}/confirm"
    first = client.post(url, json=_cancel_body(mirror_a))
    assert first.status_code == 200, first.text
    before = _raw(world.db, core_a.id)
    assert before.cancel_reason == REASON  # RED today: the first press cancelled nothing

    again = client.post(url, json=_cancel_body(mirror_a, reason="a different reason"))

    assert again.status_code == 409, again.text
    after = _raw(world.db, core_a.id)
    assert after.cancelled_at == before.cancelled_at
    assert after.cancel_reason == REASON


# ---------------------------------------------------------------- AC-C9, C12


def test_c9_one_press_confirms_b_and_cancels_a_then_a_later_press_skips_a(api):
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, _p = w["a"]
    _open_core, open_line = w["open"]
    from app.models.project_so import SOLineAllocation  # noqa: F401

    result = _confirm_all(
        client,
        w["order"],
        **_cancel_body(mirror_a, lines=[_line_payload(open_line.id, buy_qty="5")]),
    )

    assert result["ok"] is True, result
    assert [r["line_no"] for r in result["lines_cancelled"]] == [10]
    assert [r["line_no"] for r in result["lines_confirmed"]] == [30]
    assert _raw(world.db, core_a.id).line_status == "cancelled"

    # A later press on the same order, naming A again with the unchanged line B: A is
    # skipped (plan qty 0, `plan_qty_of`), no failing line, no 409.
    again = client.post(
        f"{BASE}/sales-orders/{w['order'].id}/confirm",
        json={"lines": [_line_payload(open_line.id, buy_qty="5"),
                        _line_payload(mirror_a.id)]},
    )
    assert again.status_code == 200, again.text
    assert again.json().get("lines_fulfilled_skipped", 0) >= 1
    assert not again.json().get("failing_lines")


def _built_batch_on_open_line(client, world, w):
    """A real batch built by `build_batch` (pattern: test_planning_change_apply_on_board.py
    `_two_order_batch`, :1309-1352): the open line is confirmed at 5, the book then moves it
    to 7, and the batch carries that QTY_CHANGED row. The board press composes buy 7."""
    from app.services import planning_change_service
    from app.services.scm.outstanding_diff import QTY_CHANGED, Change, Diff, Line

    core, line = w["open"]
    core_so, order, db = w["core_so"], w["order"], world.db
    assert _confirm_all(
        client, order, lines=[_line_payload(line.id, buy_qty="5")]
    )["ok"]
    core.qty_ordered = Decimal("7")
    line.qty = Decimal("7")
    db.commit()
    before = Line(doc_number=core_so.so_number, item_code="ZZT-ITEM", location="ZZT",
                  qty=5.0, required_date=None, row_ref=str(core.id))
    after = Line(doc_number=core_so.so_number, item_code="ZZT-ITEM", location="ZZT",
                 qty=7.0, required_date=None, row_ref="1")
    change = Change(QTY_CHANGED, core_so.so_number, "ZZT-ITEM", "ZZT", before=before, after=after)
    batch = planning_change_service.build_batch(
        db, Diff(scope_documents=(core_so.so_number,), changes=[change]),
        applied_line_ids={id(change): str(core.id)},
        order_ids={core_so.so_number: str(order.id)}, actor=world.eling,
        import_job_id=None, file_name="Outstanding SO.xlsx",
    )
    db.commit()
    assert batch is not None
    return batch


def _open_batch_naming(world, w, line_key, client=None):
    """An OPEN planning-change batch with one pending `qty_up` row on `w[line_key]`'s line."""
    from .test_fulfilment_line_draft_route import _planning_change_row
    from app.services.planning_change_service import PLANNING_CHANGE_STATE_PENDING

    if line_key == "open":
        return _built_batch_on_open_line(client, world, w)
    else:
        core, line, product = w[line_key]
    _row, batch = _planning_change_row(
        world.db, order=w["order"], line=line, core_line=core, product=product,
        applied_state=PLANNING_CHANGE_STATE_PENDING, batch_applied_at=None,
    )
    world.db.commit()
    return batch


def test_c12_a_cancel_of_a_line_the_batch_names_beside_its_batch_id_is_422(api):
    """AC-C12 (round 8, option b): the 422 now needs the batch to NAME the cancelled line.
    Was: any `cancelled_lines` beside a `batch_id` (a bare fake batch id) was refused."""
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, _p = w["a"]
    batch = _open_batch_naming(world, w, "a")
    response = client.post(
        f"{BASE}/sales-orders/{w['order'].id}/confirm",
        json=dict(_cancel_body(mirror_a), batch_id=str(batch.id)),
    )
    assert response.status_code == 422, response.text
    assert _status(world.db, core_a.id) == "closed"
    assert _raw(world.db, core_a.id).cancelled_at is None


def test_c23_a_cancel_of_a_line_the_batch_does_not_name_rides_along_with_the_batch(api):
    """AC-C23 (round 8, option b): batch names the open line; A is not in it."""
    from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow

    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    _open_core, open_line = w["open"]
    db = world.db
    inquiry = _inquiry(db, world.company_id, w["order"].id)
    ack_a = _oi_row(db, world.company_id, inquiry.id, mirror_a.id, product_a.product_code, "36")
    batch = _open_batch_naming(world, w, "open", client)
    batch_id = str(batch.id)

    response = client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={"orders": [dict(
            _cancel_body(mirror_a, lines=[_line_payload(open_line.id, buy_qty="7")]),
            pso_id=w["order"].id, batch_id=batch_id,
        )]},
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert [r["line_no"] for r in result["lines_cancelled"]] == [10]
    raw = _raw(db, core_a.id)
    assert raw.line_status == "cancelled"
    assert raw.cancelled_at is not None
    assert raw.cancel_reason == REASON
    db.expire_all()
    db.refresh(ack_a)
    assert ack_a.state == INQUIRY_CANCELLED
    # The batch applied exactly as it would without the cancel.
    rows = db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch_id).all()
    assert rows and all(r.applied_state == "applied" for r in rows)
    assert db.get(PlanningChangeBatch, batch_id).applied_at is not None


def test_c25_a_superseded_row_on_the_cancelled_line_does_not_make_the_batch_name_it(api):
    """AC-C25: the batch's only row on A is `superseded` (replaced by a later row), so the
    batch no longer names A and the cancel rides along like C23."""
    from app.models.planning_change import PlanningChangeRow
    from .test_fulfilment_line_draft_route import _planning_change_row

    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    _open_core, open_line = w["open"]
    db = world.db
    batch = _open_batch_naming(world, w, "open", client)
    batch_id = str(batch.id)
    row, _own = _planning_change_row(
        db, order=w["order"], line=mirror_a, core_line=core_a, product=product_a,
        applied_state="superseded", batch_applied_at=None,
    )
    row.batch_id = batch_id
    db.commit()

    response = client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={"orders": [dict(
            _cancel_body(mirror_a, lines=[_line_payload(open_line.id, buy_qty="7")]),
            pso_id=w["order"].id, batch_id=batch_id,
        )]},
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert [r["line_no"] for r in result["lines_cancelled"]] == [10]
    assert _raw(db, core_a.id).line_status == "cancelled"
    live = (
        db.query(PlanningChangeRow)
        .filter(PlanningChangeRow.batch_id == batch_id, PlanningChangeRow.applied_state != "superseded")
        .all()
    )
    assert live and all(r.applied_state == "applied" for r in live)


def test_c26_a_cancel_only_press_with_a_batch_id_cancels_and_leaves_the_batch_pending(api):
    """AC-C26 [RED]: `lines: []`, `cancelled_lines: [A]`, `batch_id` = a pending batch that
    does not name A. The press cancels A; it did nothing for the batch, so the batch stays
    pending (applied_at None) and no decision revision is written."""
    from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow

    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    db = world.db
    inquiry = _inquiry(db, world.company_id, w["order"].id)
    ack_a = _oi_row(db, world.company_id, inquiry.id, mirror_a.id, product_a.product_code, "36")
    batch = _open_batch_naming(world, w, "open", client)
    batch_id = str(batch.id)
    decisions_before = _decision_count(db, w["order"])

    response = client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={"orders": [dict(_cancel_body(mirror_a), pso_id=w["order"].id, batch_id=batch_id)]},
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert [r["line_no"] for r in result["lines_cancelled"]] == [10]
    raw = _raw(db, core_a.id)
    assert raw.line_status == "cancelled"
    assert raw.cancelled_at is not None
    assert raw.cancel_reason == REASON
    db.expire_all()
    db.refresh(ack_a)
    assert ack_a.state == INQUIRY_CANCELLED
    rows = db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch_id).all()
    assert rows and all(r.applied_state != "applied" for r in rows)
    assert db.get(PlanningChangeBatch, batch_id).applied_at is None
    assert _decision_count(db, w["order"]) == decisions_before


def test_c27_a_real_batch_refusal_beside_a_cancel_still_rolls_the_cancel_back(api, monkeypatch):
    """AC-C27 [GUARD]: the batch order fails INSIDE `planning_change_service.apply` (it lands
    in `failed_orders` with a reason), with a valid composition beside the cancel. The press
    must answer the failure, not a cancel-only success: A stays closed, batch not applied."""
    from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
    from app.services import planning_change_service

    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, _p = w["a"]
    _open_core, open_line = w["open"]
    db = world.db
    batch = _open_batch_naming(world, w, "open", client)
    batch_id = str(batch.id)

    def _boom(*_a, **_k):
        raise RuntimeError("ZZT apply refused the order")

    monkeypatch.setattr(planning_change_service, "_apply_one_order", _boom)

    response = client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={"orders": [dict(
            _cancel_body(mirror_a, lines=[_line_payload(open_line.id, buy_qty="7")]),
            pso_id=w["order"].id, batch_id=batch_id,
        )]},
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert "ZZT apply refused the order" in (result.get("error") or ""), result
    assert _status(db, core_a.id) == "closed"
    assert _raw(db, core_a.id).cancelled_at is None
    db.expire_all()
    rows = db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch_id).all()
    assert rows and all(r.applied_state != "applied" for r in rows)
    assert db.get(PlanningChangeBatch, batch_id).applied_at is None


def test_c24_a_refused_cancel_beside_a_batch_press_writes_neither(api):
    """AC-C24 (GUARD: the cancel validates before the batch, so this passes once C23's
    ride-along exists and must keep passing): A is already cancelled (409), the batch names
    the open line only; the batch stays pending and nothing is rewritten."""
    from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow

    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, _p = w["a"]
    _open_core, open_line = w["open"]
    db = world.db
    core_a.line_status = "cancelled"
    core_a.cancelled_at = datetime(2026, 10, 1, 9, 0, 0)
    core_a.cancel_reason = "first reason"
    batch = _open_batch_naming(world, w, "open", client)
    batch_id = str(batch.id)
    before = _raw(db, core_a.id)

    response = client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={"orders": [dict(
            _cancel_body(mirror_a, lines=[_line_payload(open_line.id, buy_qty="7")]),
            pso_id=w["order"].id, batch_id=batch_id,
        )]},
    )

    result = response.json()["results"][0] if response.status_code == 200 else {}
    assert response.status_code != 200 or result.get("ok") is False, response.text
    after = _raw(db, core_a.id)
    assert after.cancelled_at == before.cancelled_at
    assert after.cancel_reason == "first reason"
    rows = db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch_id).all()
    assert rows and all(r.applied_state != "applied" for r in rows)
    assert db.get(PlanningChangeBatch, batch_id).applied_at is None


# ---------------------------------------------------------------- AC-C8 board read


def test_c8_board_contribution_reads_cancelled_with_who_when_why(api):
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    _confirm_all(client, w["order"], **_cancel_body(mirror_a))

    board = _board(client, w["core_so"])

    row = next(
        c for c in board["contributions"] if c["item_code"] == product_a.product_code
    )
    assert row["cancelled"] is True
    assert row["cancelled_at"]
    assert row["cancel_reason"] == REASON
    assert row["cancelled_by"]
    assert str(world.eling) not in str(row["cancelled_by"])  # a name, never the id


# ---------------------------------------------------------------- AC-C10 / C11 ingest

from .test_ingest_documents import (  # noqa: E402
    INGEST_SO,
    _so_line,
    _so_record,
    env,  # noqa: F401 - pytest fixture
)


def _mark_cancelled(env_, line_id, reason):
    env_.db.execute(
        text(
            "UPDATE sales_order_lines SET line_status = 'cancelled', cancelled_at = :t, "
            "cancel_reason = :r WHERE id = :i"
        ),
        {"t": datetime(2026, 10, 1, 9, 0, 0), "r": reason, "i": str(line_id)},
    )
    env_.db.flush()


def test_c10_a_re_push_keeps_a_cancelled_at_line_cancelled(env):
    line = _so_line(env, qty_ordered=36, qty_delivered=36)
    record = _so_record(env, lines=[line])
    env.post(INGEST_SO, [record])
    header = env.header("sales_orders", record["source_ref"])
    line_id = env.so_lines(header["id"])[0]["id"]
    _mark_cancelled(env, line_id, REASON)

    res = env.post(INGEST_SO, [dict(record, lines=[dict(line, unit_price=9)])])

    assert res.json()["records"][0]["outcome"] in ("updated", "unchanged"), res.text
    row = _raw(env.db, line_id)
    assert row.line_status == "cancelled"
    assert row.cancelled_at is not None
    assert row.cancel_reason == REASON


def test_c11_a_push_with_a_cancelled_header_still_cancels_every_line(env):
    record = _so_record(
        env, lines=[_so_line(env), _so_line(env, product_ref=env.product2_ref)]
    )
    env.post(INGEST_SO, [record])
    header = env.header("sales_orders", record["source_ref"])

    env.post(INGEST_SO, [dict(record, status="cancelled")])

    lines = env.so_lines(header["id"])
    assert len(lines) == 2
    assert {line["line_status"] for line in lines} == {"cancelled"}
    # RED today only through the three columns: they must exist (and stay NULL here).
    assert "cancel_reason" in lines[0]


# ---------------------------------------------------------------- AC-C13 migration


def test_c13_the_three_columns_exist_on_the_model():
    for name in ("cancelled_at", "cancelled_by_id", "cancel_reason"):
        column = getattr(SalesOrderLine, name, None)
        assert column is not None, f"SalesOrderLine.{name} missing"
        assert column.property.columns[0].nullable is True


def test_c13_the_migration_graph_has_a_single_head():
    from .test_alembic_revision_ids import _script_directory

    heads = _script_directory().get_heads()
    assert len(heads) == 1, f"expected a single alembic head, found {heads}"


# ---------------------------------------------------------------- round 2: C14, C15, C16


def test_c14_a_reason_over_500_characters_is_422_and_nothing_changes(api):
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, _p = w["a"]
    response = client.post(
        f"{BASE}/sales-orders/{w['order'].id}/confirm",
        json=_cancel_body(mirror_a, reason="x" * 501),
    )
    assert response.status_code == 422, response.text
    assert _status(world.db, core_a.id) == "closed"
    assert _raw(world.db, core_a.id).cancelled_at is None


def test_c15_the_same_line_in_cancelled_lines_and_lines_is_422_and_writes_nothing(api):
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, _p = w["a"]
    before = world.db.query(SOSupplyDecision).filter(
        SOSupplyDecision.project_sales_order_id == w["order"].id
    ).count()
    response = client.post(
        f"{BASE}/sales-orders/{w['order'].id}/confirm",
        json=_cancel_body(mirror_a, lines=[_line_payload(mirror_a.id, buy_qty="5")]),
    )
    assert response.status_code == 422, response.text
    assert _status(world.db, core_a.id) == "closed"
    assert _raw(world.db, core_a.id).cancelled_at is None
    world.db.expire_all()
    after = world.db.query(SOSupplyDecision).filter(
        SOSupplyDecision.project_sales_order_id == w["order"].id
    ).count()
    assert after == before


def test_c16_a_nameless_user_never_surfaces_as_an_email_on_the_board(api):
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    from app.models.user import User

    actor = world.db.get(User, str(world.eling))
    actor.name = None
    world.db.commit()
    _confirm_all(client, w["order"], **_cancel_body(mirror_a))

    board = _board(client, w["core_so"])

    row = next(
        c for c in board["contributions"] if c["item_code"] == product_a.product_code
    )
    assert row["cancelled"] is True
    assert row["cancelled_by"] != ""
    assert "@" not in str(row["cancelled_by"])


# ---------------------------------------------------------------- round 3: C17 to C21


def _decision_count(db, order):
    db.expire_all()
    return (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .count()
    )


def test_c17_a_preview_confirm_all_echoes_the_cancel_but_writes_nothing(api):
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    world.db.add(
        SOSupplyDecisionDraft(
            sales_order_id=w["core_so"].id,
            core_line_id=core_a.id,
            line_no=10,
            item_code=product_a.product_code,
            bucket_key="2026-W41",
            decision={"verdict": "cancelled", "reason": REASON},
            saved_by=world.eling,
        )
    )
    world.db.commit()
    response = client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={"preview": True, "orders": [dict(_cancel_body(mirror_a), pso_id=w["order"].id)]},
    )
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert [r["line_no"] for r in result["lines_cancelled"]] == [10]
    row = _raw(world.db, core_a.id)
    assert row.line_status == "closed"
    assert row.cancelled_at is None
    assert row.cancel_reason is None
    world.db.expire_all()
    assert (
        world.db.query(SOSupplyDecisionDraft)
        .filter(SOSupplyDecisionDraft.core_line_id == str(core_a.id))
        .count()
        == 1
    )


def test_c18_a_refused_line_beside_a_cancel_on_the_single_route_cancels_nothing(api):
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, _p = w["a"]
    _open_core, open_line = w["open"]
    response = client.post(
        f"{BASE}/sales-orders/{w['order'].id}/confirm",
        # open qty is 5: components that add up to 3 are refused by the recheck.
        json=_cancel_body(mirror_a, lines=[_line_payload(open_line.id, buy_qty="3")]),
    )
    assert response.status_code in (409, 422), response.text
    assert _status(world.db, core_a.id) == "closed"
    assert _raw(world.db, core_a.id).cancelled_at is None


def test_c19_a_re_push_lands_the_new_price_while_the_line_stays_cancelled(env):
    line = _so_line(env, qty_ordered=36, qty_delivered=36)
    record = _so_record(env, lines=[line])
    env.post(INGEST_SO, [record])
    header = env.header("sales_orders", record["source_ref"])
    line_id = env.so_lines(header["id"])[0]["id"]
    _mark_cancelled(env, line_id, REASON)

    env.post(INGEST_SO, [dict(record, lines=[dict(line, unit_price=9)])])

    env.db.expire_all()
    price = env.db.execute(
        text("SELECT unit_price FROM sales_order_lines WHERE id = :i"), {"i": str(line_id)}
    ).scalar()
    assert Decimal(str(price)) == Decimal("9")
    assert _raw(env.db, line_id).line_status == "cancelled"


def test_c20_a_line_in_an_open_planning_change_is_still_covered_for_cancel(api):
    from .test_fulfilment_line_draft_route import _planning_change_row
    from app.services.planning_change_service import PLANNING_CHANGE_STATE_PENDING

    client, world, _core_so, order, core_1, line_1, _c2, _l2 = _covered_two_line_world(api)
    _planning_change_row(
        world.db, order=order, line=line_1, core_line=core_1, product=world.product,
        applied_state=PLANNING_CHANGE_STATE_PENDING, batch_applied_at=None,
    )
    world.db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm", json=_cancel_body(line_1)
    )

    assert response.status_code == 409, response.text
    assert _raw(world.db, core_1.id).line_status != "cancelled"


def test_c21_a_refused_line_is_held_back_and_the_cancel_is_still_written(api):
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, _p = w["a"]
    _open_core, open_line = w["open"]

    response = client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={
            "orders": [
                dict(
                    _cancel_body(mirror_a, lines=[_line_payload(open_line.id, buy_qty="3")]),
                    pso_id=w["order"].id,
                )
            ]
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert [r["line_no"] for r in result["lines_cancelled"]] == [10]
    assert [r["line_no"] for r in result["lines_held_back"]] == [30]
    assert _raw(world.db, core_a.id).line_status == "cancelled"
