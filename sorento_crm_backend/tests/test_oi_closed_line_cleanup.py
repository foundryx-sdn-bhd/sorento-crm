"""OI-CLOSED-LINE one-off clean-up script (Phase 2 red tests, AC-11 to AC-14).

Contract: `documentation/plans/scm/oi-closed-line-acceptance-criteria.md` and
`PLAN-oi-closed-line.md` change 4. Interface under test (does not exist yet, so these fail
on the missing module until the coder lands it):

    scripts.oneoff.oi_closed_line_cleanup.run(
        db, inquiry_no: str, apply: bool, cancel_balance_items: Sequence[str] = ()
    ) -> {"rows": [{row_id, item_code, verb, qty, ordered, delivered, reason, action}],
          "applied": bool}
    scripts.oneoff.oi_closed_line_cleanup.main(argv)   # --inquiry, --apply, --cancel-balance

`rows` lists the rows the run changes (or, in a dry run, would change) with `action ==
"cancel"`; a row the script leaves alone is never reported with that action.

World (one OI, raised on a project SO, every FK seeded here, fake codes only):
  A  ORDER raised, its line has a planning-change row superseded "the sales-order line is closed"
  B  ORDER_BACK raised, its core line is `cancelled`
  C  ORDER raised on a plain open line (nothing superseded)          -> untouched
  D  ORDER placed / E partly_linked / F actioned on closed-superseded lines -> never touched
  G  CANCEL_BALANCE raised + H ORDER placed, both of item CB          -> only G with --cancel-balance
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
from app.models.project_so import (
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_PARTLY_LINKED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_CANCEL_BALANCE,
    IV_ORDER,
    IV_ORDER_BACK,
    OrderInquiry,
    OrderInquiryRow,
)

from tests.test_order_inquiry_handover_automation import (
    _captured_dispatches,
    _handover_calls,
    _register,
)
from tests.test_planning_changes import (  # noqa: F401  (api is the fixture)
    _core_line,
    _core_so,
    _project_line,
    _project_so,
    api,
)

CLOSED_REASON = "the sales-order line is closed"
CB_ITEM = "ZZT-CB-ITEM"


def _hex() -> str:
    return uuid.uuid4().hex[:8]


class _Cleanup:
    pass


def _world(api, monkeypatch):
    client, world = api
    db = world.db
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    core_so = _core_so(db, world.company_id)
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    inquiry = OrderInquiry(
        id=str(uuid.uuid4()), company_id=world.company_id,
        project_sales_order_id=order.id, inquiry_no=f"OI-ZZT-{_hex()}", state=INQUIRY_RAISED,
    )
    db.add(inquiry)
    batch = PlanningChangeBatch(
        id=str(uuid.uuid4()), company_id=world.company_id, source_kind="so_book_upload",
        applied_at=datetime(2026, 10, 6, 3, 0),
    )
    db.add(batch)
    db.flush()

    w = _Cleanup()
    w.client, w.world, w.db, w.calls, w.inquiry, w.order = client, world, db, calls, inquiry, order
    w.rows = {}
    n = [0]

    def add(key, *, verb=IV_ORDER, state=INQUIRY_RAISED, superseded=False, core_status="open",
            item_code=None, qty="40", ordered="40", delivered="0",
            row_created_at=datetime(2026, 10, 1, 9, 0)):
        n[0] += 1
        core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=ordered,
                          qty_delivered=delivered, required_date=date(2027, 3, 1))
        core.line_status = core_status
        pline = _project_line(db, order, line_no=n[0], product=world.product, core_line=core)
        row = OrderInquiryRow(
            id=str(uuid.uuid4()), company_id=world.company_id, order_inquiry_id=inquiry.id,
            so_line_id=pline.id, item_code=item_code or f"ZZT-IT-{key}", qty=Decimal(qty),
            verb=verb, state=state, ack_state="awaiting", note=f"ZZT note {key}",
            created_at=row_created_at,
        )
        db.add(row)
        if superseded:
            db.add(PlanningChangeRow(
                id=str(uuid.uuid4()), company_id=world.company_id, batch_id=batch.id,
                project_sales_order_id=order.id, project_line_id=pline.id, core_line_id=core.id,
                line_no=n[0], item_code=row.item_code, kind="delayed", facts_json={},
                decision="confirm", applied_state="superseded", applied_reason=CLOSED_REASON,
                created_at=datetime(2026, 10, 6, 3, 0),
            ))
        db.flush()
        w.rows[key] = row

    add("A", superseded=True, core_status="closed", delivered="40")
    add("B", verb=IV_ORDER_BACK, core_status="cancelled")
    add("C")
    add("D", state=INQUIRY_PLACED, superseded=True, core_status="closed", delivered="40")
    add("E", state=INQUIRY_PARTLY_LINKED, superseded=True, core_status="closed", delivered="40")
    add("F", state=INQUIRY_ACTIONED, superseded=True, core_status="closed", delivered="40")
    add("G", verb=IV_CANCEL_BALANCE, item_code=CB_ITEM, qty="12")
    add("H", state=INQUIRY_PLACED, item_code=CB_ITEM)
    db.commit()
    calls.clear()
    return w


def _snapshot(db, w):
    db.expire_all()
    return {
        k: (r.state, r.qty, r.note, r.ack_state, r.verb) for k, r in w.rows.items()
    }


def _run(w, **kwargs):
    from scripts.oneoff import oi_closed_line_cleanup

    out = oi_closed_line_cleanup.run(w.db, w.inquiry.inquiry_no, **kwargs)
    w.db.commit()
    return out


def _cancelled_ids(out):
    return {str(r["row_id"]) for r in out["rows"] if r["action"] == "cancel"}


def _handover_item_codes(calls):
    return sorted(
        entry["item_code"]
        for call in _handover_calls(calls)
        for entry in call["context"]["handover"]["lines"]
    )


def test_ac_11_dry_run_writes_nothing_and_reports_each_row_it_would_change(api, monkeypatch):
    w = _world(api, monkeypatch)
    before = _snapshot(w.db, w)

    out = _run(w, apply=False)

    assert out["applied"] is False
    assert _snapshot(w.db, w) == before
    assert _handover_calls(w.calls) == []
    assert _cancelled_ids(out) == {str(w.rows["A"].id), str(w.rows["B"].id)}
    a = next(r for r in out["rows"] if str(r["row_id"]) == str(w.rows["A"].id))
    assert a["item_code"] == w.rows["A"].item_code
    assert a["verb"] == IV_ORDER
    assert Decimal(str(a["qty"])) == Decimal("40")
    assert Decimal(str(a["ordered"])) == Decimal("40")
    assert Decimal(str(a["delivered"])) == Decimal("40")
    assert a["reason"], a
    b = next(r for r in out["rows"] if str(r["row_id"]) == str(w.rows["B"].id))
    assert b["verb"] == IV_ORDER_BACK


def test_ac_12_apply_cancels_dropped_order_rows_and_hands_them_over_in_one_email(
    api, monkeypatch
):
    w = _world(api, monkeypatch)
    before = _snapshot(w.db, w)

    out = _run(w, apply=True)

    assert out["applied"] is True
    after = _snapshot(w.db, w)
    for key in ("A", "B"):
        state, _qty, note, _ack, _verb = after[key]
        assert state == INQUIRY_CANCELLED, key
        assert "dropped" in (note or ""), (key, note)
    # Placed, partly linked, actioned, plain open, and the CANCEL_BALANCE pair: untouched.
    for key in ("C", "D", "E", "F", "G", "H"):
        assert after[key] == before[key], key
    handover = _handover_calls(w.calls)
    assert len(handover) == 1, [c["trigger_type"] for c in w.calls]
    assert _handover_item_codes(w.calls) == sorted(
        [w.rows["A"].item_code, w.rows["B"].item_code]
    )


def test_ac_13_cancel_balance_item_also_cancels_its_cancel_balance_row_only(api, monkeypatch):
    w = _world(api, monkeypatch)

    out = _run(w, apply=True, cancel_balance_items=[CB_ITEM])

    after = _snapshot(w.db, w)
    assert after["G"][0] == INQUIRY_CANCELLED, "the CANCEL_BALANCE row of the item"
    assert after["H"][0] == INQUIRY_PLACED, "the placed ORDER row of the same item stays"
    assert after["H"][2] == f"ZZT note H"
    assert str(w.rows["G"].id) in _cancelled_ids(out)
    assert after["A"][0] == INQUIRY_CANCELLED and after["B"][0] == INQUIRY_CANCELLED
    assert len(_handover_calls(w.calls)) == 1
    assert CB_ITEM in _handover_item_codes(w.calls)


def test_ac_14_a_second_apply_changes_nothing(api, monkeypatch):
    w = _world(api, monkeypatch)
    _run(w, apply=True, cancel_balance_items=[CB_ITEM])
    settled = _snapshot(w.db, w)
    w.calls.clear()

    again = _run(w, apply=True, cancel_balance_items=[CB_ITEM])

    assert _snapshot(w.db, w) == settled
    assert _cancelled_ids(again) == set()
    assert _handover_calls(w.calls) == []


def test_cli_requires_an_inquiry_number():
    from scripts.oneoff import oi_closed_line_cleanup

    with pytest.raises(SystemExit) as exc:
        oi_closed_line_cleanup.main([])
    assert exc.value.code not in (0, None)


def test_a_row_raised_after_the_dropped_decision_was_applied_is_not_cancelled(api, monkeypatch):
    """Scoping: CS later re-decided the line with Buy > 0, so a NEW raised ORDER row exists on a
    line that still carries the old superseded-closed batch row. Only rows raised before that
    dropped decision belong to the clean-up."""
    from scripts.oneoff import oi_closed_line_cleanup  # noqa: F401

    w = _world(api, monkeypatch)
    # Reuse line D's superseded row: a second ORDER row on a line whose dropped decision is
    # older than the row (batch applied 6 Oct 03:00, row raised 9 Oct).
    line_id = w.rows["D"].so_line_id
    fresh = OrderInquiryRow(
        id=str(uuid.uuid4()), company_id=w.world.company_id, order_inquiry_id=w.inquiry.id,
        so_line_id=line_id, item_code="ZZT-IT-NEW", qty=Decimal("25"), verb=IV_ORDER,
        state=INQUIRY_RAISED, ack_state="awaiting", note="ZZT re-decided",
        created_at=datetime(2026, 10, 9, 9, 0),
    )
    w.db.add(fresh)
    w.db.commit()
    w.calls.clear()

    out = _run(w, apply=True)

    w.db.expire_all()
    assert w.db.get(OrderInquiryRow, fresh.id).state == INQUIRY_RAISED
    assert str(fresh.id) not in _cancelled_ids(out)
    assert _cancelled_ids(out) == {str(w.rows["A"].id), str(w.rows["B"].id)}


def test_the_cancel_note_is_appended_to_the_existing_note(api, monkeypatch):
    w = _world(api, monkeypatch)

    _run(w, apply=True)

    w.db.expire_all()
    note = w.db.get(OrderInquiryRow, w.rows["A"].id).note
    assert "ZZT note A" in note, note
    assert "dropped" in note, note


def test_an_inquiry_number_shared_by_two_companies_is_refused_without_a_company(api, monkeypatch):
    from app.models.base import set_company_scope
    from app.models.company import Company
    from app.models.project_so import ProjectSalesOrder
    from scripts.oneoff import oi_closed_line_cleanup

    w = _world(api, monkeypatch)
    db = w.db
    other_id = str(uuid.uuid4())
    db.add(Company(id=other_id, name="ZZT Other Co", code=f"ZZC{other_id[:6]}"))
    db.flush()
    other_pso = ProjectSalesOrder(
        provisional_ref=f"ZZT-PSO-{_hex()}", company_id=other_id,
    )
    db.add(other_pso)
    db.flush()
    db.add(OrderInquiry(
        id=str(uuid.uuid4()), company_id=other_id, project_sales_order_id=other_pso.id,
        inquiry_no=w.inquiry.inquiry_no, state=INQUIRY_RAISED,
    ))
    db.commit()
    company_ids = frozenset({w.world.company_id, other_id})
    set_company_scope(db, None)
    try:
        with pytest.raises((Exception, SystemExit)):
            oi_closed_line_cleanup.run(db, w.inquiry.inquiry_no, apply=False)
        out = oi_closed_line_cleanup.run(
            db, w.inquiry.inquiry_no, apply=False, company_code="SRT"
        )
        assert _cancelled_ids(out) == {str(w.rows["A"].id), str(w.rows["B"].id)}
    finally:
        set_company_scope(db, frozenset({w.world.company_id}))


def test_cli_accepts_a_company_argument(api, monkeypatch):
    from scripts.oneoff import oi_closed_line_cleanup

    w = _world(api, monkeypatch)

    code = oi_closed_line_cleanup.main(
        ["--inquiry", w.inquiry.inquiry_no, "--company", "SRT"], db=w.db
    )

    assert code in (0, None)


def test_a_row_updated_in_place_by_a_later_buy_decision_is_not_cancelled(api, monkeypatch):
    """A re-decide with Buy > 0 on a line with one raised ORDER row updates that row IN PLACE, so
    it keeps its old created_at (1 Oct). A later active decision (confirmed after the dropped
    batch applied, 6 Oct) that covers the line with a Buy makes the row live: not cancelled,
    not reported."""
    from app.models.project_so import SOSupplyDecision

    w = _world(api, monkeypatch)
    row = w.rows["A"]
    assert row.created_at == datetime(2026, 10, 1, 9, 0)
    w.db.add(SOSupplyDecision(
        id=str(uuid.uuid4()), company_id=w.world.company_id,
        project_sales_order_id=w.order.id, revision_no=2, state="active",
        line_snapshots=[{
            "project_line_id": str(row.so_line_id), "line_no": 1,
            "components": [{"kind": "buy", "qty": "40"}],
        }],
        confirmed_by=w.world.actor, confirmed_at=datetime(2026, 10, 8, 9, 0),
    ))
    w.db.commit()
    w.calls.clear()

    out = _run(w, apply=True)

    w.db.expire_all()
    assert w.db.get(OrderInquiryRow, row.id).state == INQUIRY_RAISED
    assert str(row.id) not in _cancelled_ids(out)


def test_a_dropped_line_only_carried_by_later_confirms_is_still_cancelled(api, monkeypatch):
    """The OI-2609-0724 shape (fake codes): the decision on line A was dropped at 03:00, then CS
    confirmed OTHER lines three times (03:50, 05:28, 07:18). Each confirm carries A's old
    snapshot forward verbatim and cancels-and-re-raises its still-raised ORDER row under the
    new revision, so the live row is born 07:18 and points at the active revision. Nothing
    re-decided A: the row is still stranded and must be cancelled."""
    from app.models.project_so import (
        DECISION_ACTIVE,
        DECISION_SUPERSEDED,
        SOSupplyDecision,
    )

    w = _world(api, monkeypatch)
    db = w.db
    a, c = w.rows["A"], w.rows["C"]
    a_snap = {
        "project_line_id": str(a.so_line_id), "line_no": 1,
        "components": [{"kind": "buy", "qty": "40"}],
    }

    def c_snap(qty):
        return {
            "project_line_id": str(c.so_line_id), "line_no": 3,
            "components": [{"kind": "buy", "qty": qty}],
        }

    revisions = [
        (1, datetime(2026, 10, 1, 9, 0), [dict(a_snap), c_snap("40")]),
        (2, datetime(2026, 10, 6, 3, 50), [dict(a_snap), c_snap("30")]),
        (3, datetime(2026, 10, 6, 5, 28), [dict(a_snap), c_snap("20")]),
        (4, datetime(2026, 10, 6, 7, 18), [dict(a_snap), c_snap("10")]),
    ]
    active = None
    for revision_no, confirmed_at, snapshots in revisions:
        last = revision_no == len(revisions)
        decision = SOSupplyDecision(
            id=str(uuid.uuid4()), company_id=w.world.company_id,
            project_sales_order_id=w.order.id, revision_no=revision_no,
            state=DECISION_ACTIVE if last else DECISION_SUPERSEDED,
            line_snapshots=snapshots, confirmed_by=w.world.actor, confirmed_at=confirmed_at,
        )
        db.add(decision)
        db.flush()
        active = decision
    # The last carry: the old raised row cancelled, a fresh one raised under revision 4.
    a.state = INQUIRY_CANCELLED
    carried = OrderInquiryRow(
        id=str(uuid.uuid4()), company_id=w.world.company_id, order_inquiry_id=w.inquiry.id,
        so_line_id=a.so_line_id, item_code=a.item_code, qty=Decimal("40"), verb=IV_ORDER,
        state=INQUIRY_RAISED, ack_state="awaiting", note="ZZT carried A",
        supply_decision_id=active.id, created_at=datetime(2026, 10, 6, 7, 18),
    )
    db.add(carried)
    db.commit()
    w.calls.clear()

    out = _run(w, apply=False)

    assert str(carried.id) in _cancelled_ids(out), out["rows"]
