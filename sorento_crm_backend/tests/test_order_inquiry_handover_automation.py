"""Order inquiry handover email to purchasing (`PLAN-scm-oi-handover-email.md`, S0-S3).

Contract: `documentation/plans/scm/scm-oi-handover-email-acceptance-criteria.md`, AC-H1 to
AC-H18. TEST-FIRST: written before the recorder/drain/trigger/template exist, so a red
here is a missing attribute (`ProjectOrderInquiryService._record_handover`, the module
constant `_HANDOVER_PENDING_KEY`, the pure function `handover_remark`), a missing trigger
type in the catalog, a missing seed migration, or simply "the automation was never
dispatched" - never an import typo or a fixture bug.

Two harnesses, matching the two things under test:

* the RECORDER/DRAIN/DISPATCH contract (AC-H1 to AC-H10, AC-H15, AC-H17, AC-H18) reuses
  `tests/test_order_inquiry_handshake.py`'s `world` / `api` fixtures wholesale, for the
  same reason `tests/test_order_inquiry_changed_with_links_automation.py` does: one
  seeding chain, the REAL database (`scm.committed_v` and the handshake columns live only
  in the migrated schema), and `AutomationService.dispatch_event` monkeypatched rather
  than exercised for real - the drain's fresh `SessionLocal()` is a genuinely separate
  connection that cannot see this suite's own rolled-back savepoint, so a real dispatch
  attempting to re-read the raised row would find nothing there;
* the TRIGGER CATALOG (AC-H12), RECIPIENT RESOLVER (AC-H11) and SEED MIGRATION + TEMPLATE
  (AC-H13/AC-H14) need no shared seeding chain at all, and use `tests/_pg_fixture.py`'s
  `blank_session()` - a blank scratch schema - instead.

AC-H6 (CHANGE SO NO) is pinned at the `_record_handover` contract boundary rather than
through a real delivery-schedule delta + amendment write, and AC-H15's "outbox row"
language is satisfied via the mocked dispatch call's ordering rather than a literal
`email_outbox` row - both narrowed deliberately; see the module-level note above each test
for why, and the tester's report for the full reasoning.
"""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.base import company_scope
from app.models.project_so import (
    ACK_REJECTED,
    AMENDMENT_PUBLISHED,
    INQUIRY_CANCELLED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_ALREADY_INBOUND,
    IV_CHANGE_SO,
    IV_DELAY,
    IV_ORDER,
    IV_ORDER_BACK,
    IV_PRE_ORDERED,
    IV_RESERVE_AND_ORDER,
    SO_STATUS_ADOPTED,
    SO_STATUS_DRAFT,
    SO_STATUS_PUBLISHED,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    SOAmendment,
)
from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody
from app.services.automation_triggers import build_order_inquiry_link
from app.services.project_order_inquiry_service import (
    _HANDOVER_COMMITTED_TX_KEY,
    _HANDOVER_PENDING_KEY,
    ProjectOrderInquiryService,
)
from app.services.project_supply_service import ProjectSupplyService

from ._pg_fixture import blank_session
from .test_order_inquiry_handshake import (
    ACK_URL,
    LINK_NOW,
    LIST,
    NOW,
    WAS,
    _as_purchasing,
    _confirm,
    _core_line,
    _core_so,
    _line_payload,
    _open_po_line,
    _product,
    _project_line,
    _project_so,
    _raise_one_row,
    _raise_two_rows,
    _uid,
    _warehouse,
    api,
    world,
)

__all__ = ["api", "world"]  # re-exported fixtures; keeps linters from calling them unused

TRIGGER = "order_inquiry_handover"


# --------------------------------------------------------------------------- #
# shared harness (mirrors test_order_inquiry_changed_with_links_automation.py) #
# --------------------------------------------------------------------------- #


def _captured_dispatches(monkeypatch) -> list[dict]:
    """Intercepts `AutomationService.dispatch_event` on the drain's fresh session."""
    calls: list[dict] = []

    def _fake_dispatch(self, trigger_type, *, context, source_kind, source_id):
        calls.append(
            {
                "trigger_type": trigger_type,
                "context": context,
                "source_kind": source_kind,
                "source_id": source_id,
            }
        )
        return {"trigger_type": trigger_type, "fired": 0, "results": []}

    monkeypatch.setattr(
        "app.services.automation_service.AutomationService.dispatch_event",
        _fake_dispatch,
    )
    return calls


def _register(world) -> None:
    """Idempotent (module-global flag), same call site the sibling suite uses -
    `TestClient(app)` without `with` never runs FastAPI's lifespan here."""
    from app.services.project_order_inquiry_service import (
        register_order_inquiry_post_commit_dispatch,
    )

    register_order_inquiry_post_commit_dispatch()


def _handover_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["trigger_type"] == TRIGGER]


def _row(**kwargs) -> SimpleNamespace:
    """A stand-in for `OrderInquiryRow`. `handover_remark` is documented (PLAN 3.3) as a
    PURE, table-tested function that reads a handful of attributes off the row - a
    lightweight namespace pins the table without a database."""
    base = dict(
        verb=None,
        qty=None,
        delivery_date=None,
        previous_qty=None,
        previous_delivery_date=None,
        cited_document=None,
        note=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# AC-H12: trigger catalog                                                     #
# --------------------------------------------------------------------------- #


def test_trigger_in_catalog():
    from app.services import automation_triggers

    spec = next(
        (s for s in automation_triggers.list_specs() if s.type == TRIGGER),
        None,
    )
    assert spec is not None, f"{TRIGGER!r} must be registered in the trigger catalog"
    assert spec.config_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


# --------------------------------------------------------------------------- #
# AC-H2/H3/H4/H5: handover_remark, table-tested and pure (PLAN 3.3)            #
# --------------------------------------------------------------------------- #


def test_handover_remark_raised_rows_use_the_verb_label():
    """AC-H2: ORDER / RESERVE & ORDER / PRE-ORDERED / ALREADY INBOUND print their own
    verb label with no `was`; ORDER BACK appends the cited document."""
    from app.services.project_order_inquiry_service import handover_remark

    assert handover_remark("raised", _row(verb=IV_ORDER), None) == "ORDER"
    assert (
        handover_remark("raised", _row(verb=IV_RESERVE_AND_ORDER), None)
        == "RESERVE & ORDER"
    )
    assert (
        handover_remark("raised", _row(verb=IV_PRE_ORDERED), None)
        == "PRE-ORDERED, DO NOT ORDER"
    )
    assert (
        handover_remark("raised", _row(verb=IV_ALREADY_INBOUND), None)
        == "ALREADY INBOUND"
    )

    order_back = handover_remark(
        "raised", _row(verb=IV_ORDER_BACK, cited_document="SPO-2026/08-0061"), None
    )
    assert order_back.startswith("ORDER BACK")
    assert "SPO-2026/08-0061" in order_back


def test_handover_remark_appends_a_cs_note_after_the_verb():
    """AC-H2: "`note` appended after ' - ' when present" (PLAN 3.3)."""
    from app.services.project_order_inquiry_service import handover_remark

    remark = handover_remark(
        "raised", _row(verb=IV_ORDER, note="Confirmed by WhatsApp"), None
    )
    assert remark == "ORDER - Confirmed by WhatsApp"


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (WAS, NOW, "ADVANCE"),  # NOW (19 Aug) is earlier than WAS (25 Aug)
        (NOW, WAS, "DELAY"),
    ],
)
def test_handover_remark_settle_date_advance_or_delay(old, new, expected):
    """AC-H3."""
    from app.services.project_order_inquiry_service import handover_remark

    row = _row(verb=IV_ORDER, qty=Decimal("10"), delivery_date=new)
    remark = handover_remark("settled", row, {"delivery_date": old})
    assert remark == expected


def test_handover_remark_settle_qty_down_cancel_balance_up_order():
    """AC-H4."""
    from app.services.project_order_inquiry_service import handover_remark

    down = handover_remark(
        "settled", _row(verb=IV_ORDER, qty=Decimal("6"), delivery_date=WAS),
        {"qty": Decimal("10")},
    )
    assert down == "CANCEL BALANCE 4 NOS"

    up = handover_remark(
        "settled", _row(verb=IV_ORDER, qty=Decimal("15"), delivery_date=WAS),
        {"qty": Decimal("10")},
    )
    assert up == "ORDER 5"


def test_handover_remark_settle_both_moved_joins_date_then_qty():
    """AC-H4's table row: "settled, both moved: date verb first, then qty phrase,
    joined by ', '" (PLAN 3.3)."""
    from app.services.project_order_inquiry_service import handover_remark

    remark = handover_remark(
        "settled",
        _row(verb=IV_ORDER, qty=Decimal("15"), delivery_date=NOW),
        {"qty": Decimal("10"), "delivery_date": WAS},
    )
    assert remark == "ADVANCE, ORDER 5"


def test_handover_remark_settle_to_zero_is_cancel_balance():
    """AC-H5."""
    from app.services.project_order_inquiry_service import handover_remark

    remark = handover_remark(
        "cancelled", _row(verb=IV_ORDER, qty=Decimal("0")), {"qty": Decimal("10")}
    )
    assert remark == "CANCEL BALANCE 10 NOS"


# --------------------------------------------------------------------------- #
# AC-H1: one dispatch per commit, however many orders it raised                #
# --------------------------------------------------------------------------- #


def test_one_dispatch_per_commit_two_orders(api, monkeypatch):
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db
    supply = ProjectSupplyService(db)

    core_so_1 = _core_so(db, world.company_id)
    core_line_1 = _core_line(
        db, core_so_1, world.product, world.warehouse, qty_ordered="10", required_date=WAS
    )
    order_1 = _project_so(
        db, world.project, so_id=core_so_1.id, autocount_doc_no=core_so_1.so_number
    )
    line_1 = _project_line(db, order_1, line_no=1, product=world.product, core_line=core_line_1)

    core_so_2 = _core_so(db, world.company_id)
    core_line_2 = _core_line(
        db, core_so_2, world.product, world.warehouse, qty_ordered="8", required_date=WAS
    )
    order_2 = _project_so(
        db, world.project, so_id=core_so_2.id, autocount_doc_no=core_so_2.so_number
    )
    line_2 = _project_line(db, order_2, line_no=1, product=world.product, core_line=core_line_2)
    db.commit()

    supply.confirm(
        order_1,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line_1.id), buy_qty="10")]),
        actor_user_id=world.cs_user,
    )
    supply.confirm(
        order_2,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line_2.id), buy_qty="8")]),
        actor_user_id=world.cs_user,
    )
    db.commit()

    matches = _handover_calls(calls)
    assert len(matches) == 1, f"expected exactly one dispatch for the one commit, got {len(matches)}"
    handover = matches[0]["context"]["handover"]
    so_numbers = {o["so_number"] for o in handover["orders"]}
    assert so_numbers == {core_so_1.so_number, core_so_2.so_number}
    assert len(handover["lines"]) == 2


# --------------------------------------------------------------------------- #
# AC-H2 (end-to-end half): raised rows carry no `was`; ORDER BACK cites its    #
# document                                                                     #
# --------------------------------------------------------------------------- #


def test_raised_rows_no_was_and_order_back_carries_cited_document(api, monkeypatch):
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db

    core_so = _core_so(db, world.company_id)
    plain_core = _core_line(
        db, core_so, world.product, world.warehouse, qty_ordered="10", required_date=WAS
    )
    order_back_core = _core_line(
        db, core_so, world.product, world.warehouse, qty_ordered="6", required_date=WAS
    )
    order = _project_so(
        db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number
    )
    plain_line = _project_line(db, order, line_no=1, product=world.product, core_line=plain_core)
    order_back_line = _project_line(
        db, order, line_no=2, product=world.product, core_line=order_back_core
    )
    db.commit()

    payload = [
        _line_payload(plain_line.id, buy_qty="10"),
        {
            **_line_payload(order_back_line.id, buy_qty="6"),
            "order_back": True,
            "cited_document": "SPO-2026/08-0061",
        },
    ]
    response = _confirm(client, order.id, payload)
    assert response.status_code == 200, response.text
    db.commit()

    matches = _handover_calls(calls)
    assert matches, "the raise must dispatch the handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    plain = next(l for l in lines if l["remark"] == "ORDER")
    assert plain["was"] is None
    order_back = next(l for l in lines if l["remark"].startswith("ORDER BACK"))
    assert order_back["was"] is None
    assert "SPO-2026/08-0061" in order_back["remark"]


# --------------------------------------------------------------------------- #
# AC-H3/H4/H5 (end-to-end half): the real settle seam records + dispatches    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("new_date", "expected_remark"),
    [(NOW, "ADVANCE"), (date(2026, 9, 10), "DELAY")],
)
def test_settle_date_earlier_is_advance_later_is_delay(api, monkeypatch, new_date, expected_remark):
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    calls.clear()  # only the settle's own dispatch matters here

    from .test_order_inquiry_handshake import _settle

    _settle(world, fixture, qty="10", required_date=new_date)

    matches = _handover_calls(calls)
    assert matches, "a settle that moves the date must dispatch the handover"
    line = matches[-1]["context"]["handover"]["lines"][0]
    assert line["remark"] == expected_remark
    assert line["was"]["delivery_date"] == WAS.strftime("%d/%m/%Y")
    assert line["delivery_date"] == new_date.strftime("%d/%m/%Y")


@pytest.mark.parametrize(
    ("new_qty", "expected_remark"),
    [("6", "CANCEL BALANCE 4 NOS"), ("15", "ORDER 5")],
)
def test_settle_qty_down_cancel_balance_up_order(api, monkeypatch, new_qty, expected_remark):
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    calls.clear()

    from .test_order_inquiry_handshake import _settle

    _settle(world, fixture, qty=new_qty, required_date=WAS)

    matches = _handover_calls(calls)
    assert matches, "a qty settle must dispatch the handover"
    line = matches[-1]["context"]["handover"]["lines"][0]
    assert line["remark"] == expected_remark
    assert line["was"]["qty"] == "10"
    assert line["qty"] == new_qty


def test_settle_to_zero_prints_cancel_balance(api, monkeypatch):
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    calls.clear()

    from .test_order_inquiry_handshake import _settle

    _settle(world, fixture, qty="0")

    matches = _handover_calls(calls)
    assert matches, "a settle to zero must dispatch the handover"
    line = matches[-1]["context"]["handover"]["lines"][0]
    assert line["qty"] == "0"
    assert line["was"]["qty"] == "10"
    assert line["remark"] == "CANCEL BALANCE 10 NOS"


# --------------------------------------------------------------------------- #
# AC-H6: CHANGE SO NO - pinned at the `_record_handover` contract boundary    #
# --------------------------------------------------------------------------- #


def test_change_so_row_carries_source_in_was(api, monkeypatch):
    """AC-H6.

    The plan itself flags (section 6, item 1) that WHICH FIELD an `IV_CHANGE_SO` row's
    raise reads for "the source order" is still to be discovered while building - the
    only real producer of this verb (`derive_for_amendment` reading a delivery-schedule
    delta's `CHANGE_REPOINT` rows) reports `field: "area_group"`,
    `from_value: "COMMON AREA"` / `to_value: "PODIUM"` (see
    `tests/test_project_so_delta.py::test_...`), not a second sales order's
    so_number/customer/project - so reproducing that whole delivery-schedule fixture
    chain here would not exercise AC-H6 as WRITTEN, only whatever the coder decides that
    ambiguity resolves to. This test instead pins the CONTRACT one level down: whatever
    the raise seam captures as `was` for a CHANGE SO NO row reaches the dispatched
    context unchanged, keyed by so_number/customer/project, while the row's own line is
    read as usual (the TARGET) at drain time.
    """
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    row = fixture["row"]
    row.verb = IV_CHANGE_SO
    world.db.flush()

    service = ProjectOrderInquiryService(world.db)
    service._record_handover(
        row,
        kind="raised",
        was={
            "so_number": "SO-SOURCE-001",
            "customer": "SOURCE CUSTOMER SDN BHD",
            "project": "Source Project",
        },
        actor_user_id=world.cs_user,
    )
    world.db.commit()

    matches = _handover_calls(calls)
    assert matches
    line = matches[-1]["context"]["handover"]["lines"][-1]
    assert line["was"] == {
        "so_number": "SO-SOURCE-001",
        "customer": "SOURCE CUSTOMER SDN BHD",
        "project": "Source Project",
    }
    assert line["so_number"] == fixture["core_so"].so_number
    assert line["remark"] == "CHANGE SO NO"


# --------------------------------------------------------------------------- #
# AC-H7: subject scope                                                        #
# --------------------------------------------------------------------------- #


def test_subject_scope_single_and_mixed_location(api, monkeypatch):
    """AC-H7.

    `ProjectSupplyService._restamp_stock_location` overwrites `line.stock_location` with
    the CONFIRMED core line's own warehouse code (`fact.own_code`, read off
    `core.warehouse_id` in `_facts_for`) on every confirm - so hand-setting
    `ProjectSalesOrderLine.stock_location` directly never survives (coder finding, 16
    Sep). The location has to be driven the way the system actually derives it: give
    each CORE line its own warehouse and let the confirm's own restamp read it back.
    """
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db
    supply = ProjectSupplyService(db)

    # One order, two lines, ONE shared warehouse -> "<location> @ <so>".
    shared_wh = _warehouse(db, f"ZZT-OIHE-{_uid()[:8]}")
    core_so = _core_so(db, world.company_id)
    line_a_core = _core_line(
        db, core_so, world.product, shared_wh, qty_ordered="10", required_date=WAS
    )
    line_b_core = _core_line(
        db, core_so, world.product, shared_wh, qty_ordered="6", required_date=WAS
    )
    order = _project_so(
        db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number
    )
    line_a = _project_line(db, order, line_no=1, product=world.product, core_line=line_a_core)
    line_b = _project_line(db, order, line_no=2, product=world.product, core_line=line_b_core)
    db.commit()

    response = _confirm(
        client, order.id,
        [_line_payload(line_a.id, buy_qty="10"), _line_payload(line_b.id, buy_qty="6")],
    )
    assert response.status_code == 200, response.text
    db.commit()

    matches = _handover_calls(calls)
    assert matches, "the raise must dispatch the handover"
    assert (
        matches[-1]["context"]["handover"]["subject_scope"]
        == f"{shared_wh.warehouse_code} @ {core_so.so_number}"
    )
    calls.clear()

    # Two DIFFERENT orders, two DIFFERENT warehouses, one commit -> mixed, no location.
    wh_c = _warehouse(db, f"ZZT-OIHE-{_uid()[:8]}")
    wh_d = _warehouse(db, f"ZZT-OIHE-{_uid()[:8]}")
    core_so_2 = _core_so(db, world.company_id)
    line_c_core = _core_line(
        db, core_so_2, world.product, wh_c, qty_ordered="4", required_date=WAS
    )
    order_2 = _project_so(
        db, world.project, so_id=core_so_2.id, autocount_doc_no=core_so_2.so_number
    )
    line_c = _project_line(db, order_2, line_no=1, product=world.product, core_line=line_c_core)

    core_so_3 = _core_so(db, world.company_id)
    line_d_core = _core_line(
        db, core_so_3, world.product, wh_d, qty_ordered="3", required_date=WAS
    )
    order_3 = _project_so(
        db, world.project, so_id=core_so_3.id, autocount_doc_no=core_so_3.so_number
    )
    line_d = _project_line(db, order_3, line_no=1, product=world.product, core_line=line_d_core)
    db.commit()

    supply.confirm(
        order_2,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line_c.id), buy_qty="4")]),
        actor_user_id=world.cs_user,
    )
    supply.confirm(
        order_3,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line_d.id), buy_qty="3")]),
        actor_user_id=world.cs_user,
    )
    db.commit()

    mixed = _handover_calls(calls)
    assert mixed, "the mixed-location write must dispatch too"
    assert (
        mixed[-1]["context"]["handover"]["subject_scope"]
        == f"{core_so_2.so_number} , {core_so_3.so_number}"
    )


# --------------------------------------------------------------------------- #
# AC-H19: a retired row (dropped from the buy list, no settle) still prints    #
# as a cancelled line; an unrelated raise in the same commit prints beside it #
# --------------------------------------------------------------------------- #


def test_retired_row_prints_cancelled_line(api, monkeypatch):
    """AC-H19.

    `_retire_uncovered_rows` retires a row whose LINE was dropped from the buy list
    entirely - CS un-decided it (`ProjectSupplyService.uncover_lines`, the same seam
    `test_order_inquiry_changed_with_links_automation.py`'s
    `test_a_dropped_lines_cascade_linked_row_dispatches_when_retired` drives) - not a
    settle and not a same-line supersede. The retired row must still print as a CANCEL
    BALANCE line: `qty` "0", `was.qty` the old qty, remark "CANCEL BALANCE <old> NOS".

    The PLAN's revised rule (16 Sep, after AC-H19 was added) is that a retire prints
    ALWAYS, with no pairing to whatever else the same commit raises - a cross-reference
    "does this retired row have an exact replacement" would need every raise in the
    commit indexed by line, which the coder found impractical. So the second half here
    is a genuinely UNRELATED line raised in the very same write (not a replacement FOR
    the retired line): both must appear as their own, separate `lines` entries.
    """
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)

    fixture = _raise_two_rows(api, first_qty="10", second_qty="6")
    world.db.commit()
    calls.clear()

    db = world.db
    supply = ProjectSupplyService(db)

    # A second, unrelated order, raised in the SAME write as the retire below.
    core_so_fresh = _core_so(db, world.company_id)
    fresh_core_line = _core_line(
        db, core_so_fresh, world.product, world.warehouse, qty_ordered="4", required_date=WAS
    )
    fresh_order = _project_so(
        db, world.project, so_id=core_so_fresh.id, autocount_doc_no=core_so_fresh.so_number
    )
    fresh_line = _project_line(
        db, fresh_order, line_no=1, product=world.product, core_line=fresh_core_line
    )
    db.flush()

    supply.uncover_lines(
        fixture["order"],
        [str(fixture["first"]["line"].id)],
        actor_user_id=world.cs_user,
        reason="CS took the line back.",
    )
    supply.confirm(
        fresh_order,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(fresh_line.id), buy_qty="4")]),
        actor_user_id=world.cs_user,
    )
    db.commit()

    world.db.refresh(fixture["first"]["row"])
    assert fixture["first"]["row"].state == INQUIRY_CANCELLED, (
        "the retire has to have actually cancelled the row for this test to mean anything"
    )

    matches = _handover_calls(calls)
    assert matches, "the retire must dispatch the handover"
    lines = matches[-1]["context"]["handover"]["lines"]

    cancelled = [l for l in lines if l["remark"] == "CANCEL BALANCE 10 NOS"]
    assert cancelled, f"no cancelled line for the retired row in {lines}"
    assert cancelled[0]["qty"] == "0"
    assert cancelled[0]["was"] == {"qty": "10"}

    fresh = [l for l in lines if l["so_number"] == core_so_fresh.so_number]
    assert fresh, "an unrelated raise in the same commit must print beside the retire, unpaired"
    assert fresh[0]["remark"] == "ORDER"


# --------------------------------------------------------------------------- #
# AC-H20: an unchanged carry is silent; a changed carry prints once as settled #
# --------------------------------------------------------------------------- #


def test_unchanged_carried_row_is_not_printed(api, monkeypatch):
    """AC-H20.

    H19's own captured output shows the gap this pins: the carried line's row goes
    through the SAME cancel-and-re-raise site as any other raise
    (`refresh_for_decision`'s `raised_row = OrderInquiryRow(...)` /
    `self._record_handover(raised_row, kind="raised", ...)`, called UNCONDITIONALLY) -
    so an UNCHANGED carry (line 2, same qty/date it already had, only line 1 was
    uncovered) currently prints as a fresh "ORDER" line with no `was`. Nothing changed
    for purchasing, so it must print NOTHING. A carry the active decision's own frozen
    snapshot genuinely restates at a DIFFERENT qty is the opposite case: it DID change,
    so it must print exactly once, but as a SETTLED line (`was.qty` set), never as a
    second bare ORDER.
    """
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)

    # -- unchanged carry: line 1 dropped, line 2 carried at the SAME qty/date. --
    fixture = _raise_two_rows(api, first_qty="10", second_qty="6")
    world.db.commit()
    calls.clear()

    supply = ProjectSupplyService(world.db)
    supply.uncover_lines(
        fixture["order"],
        [str(fixture["first"]["line"].id)],
        actor_user_id=world.cs_user,
        reason="CS took the line back.",
    )
    world.db.commit()

    matches = _handover_calls(calls)
    assert matches, "the retire must still dispatch the handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    unchanged_carry = [l for l in lines if l["qty"] == "6"]
    assert unchanged_carry == [], (
        f"an unchanged carry must print nothing for purchasing, found {lines}"
    )

    # -- a variant: the carry's qty DID change - the row's live qty has drifted from
    # what the active decision's own frozen snapshot carries forward for it (the same
    # kind of drift an earlier settle-in-place could leave behind). It must print
    # exactly once, as a settled line carrying `was.qty`, never as a second bare ORDER.
    fixture2 = _raise_two_rows(api, first_qty="10", second_qty="6")
    world.db.commit()
    second_row = fixture2["second"]["row"]
    second_row.qty = Decimal("9")
    world.db.flush()
    world.db.commit()
    calls.clear()

    ProjectSupplyService(world.db).uncover_lines(
        fixture2["order"],
        [str(fixture2["first"]["line"].id)],
        actor_user_id=world.cs_user,
        reason="CS took the line back.",
    )
    world.db.commit()

    matches2 = _handover_calls(calls)
    assert matches2, "the retire must dispatch the handover"
    lines2 = matches2[-1]["context"]["handover"]["lines"]
    changed_carry = [l for l in lines2 if l["was"] == {"qty": "9"}]
    assert len(changed_carry) == 1, (
        f"a carry that actually changed qty must print exactly once with was.qty, got {lines2}"
    )
    assert changed_carry[0]["qty"] == "6"


# --------------------------------------------------------------------------- #
# AC-H21: a confirm inside a savepoint (planning-change apply, book upload)    #
# dispatches exactly once and leaves nothing pending                          #
# --------------------------------------------------------------------------- #


def test_confirm_inside_savepoint_dispatches_once(api, monkeypatch):
    """AC-H21 (review round 1, B1).

    `planning_change_service.apply` wraps each order's write in its own
    `db.begin_nested()` / `savepoint.commit()` (see that module around line 4715), and
    the outstanding-book upload does the same. `_record_handover` already tags the entry
    it queues with `_transaction_chain(self.db)` - `get_nested_transaction() or
    get_transaction()` - so it correctly names the SAVEPOINT, not the root. The drain
    side does not match: `_mark_handover_transaction_committed` (the `after_commit`
    listener that records which transaction just concluded via commit, so
    `_fire_pending_handover`'s `after_transaction_end` can tell a commit from a
    rollback) calls bare `session.get_transaction()`, which returns the ROOT
    transaction ALWAYS, per SQLAlchemy's own contract - never the savepoint that is
    actually committing. So the savepoint's own commit is never recorded as "committed",
    `_fire_pending_handover` reads that as a rollback when the savepoint later closes,
    and the entry is left stranded on `session.info` forever - reviewer's probe on HEAD
    reports exactly that: 0 dispatches, 1 stranded pending item.
    """
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db
    supply = ProjectSupplyService(db)

    core_so = _core_so(db, world.company_id)
    core_line = _core_line(
        db, core_so, world.product, world.warehouse, qty_ordered="10", required_date=WAS
    )
    order = _project_so(
        db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number
    )
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    savepoint = db.begin_nested()
    supply.confirm(
        order,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line.id), buy_qty="10")]),
        actor_user_id=world.cs_user,
    )
    savepoint.commit()
    db.commit()

    matches = _handover_calls(calls)
    assert len(matches) == 1, f"expected exactly one dispatch, got {len(matches)}"
    assert not db.info.get(_HANDOVER_PENDING_KEY), (
        "nothing may remain pending on the session once the root transaction commits"
    )
    assert not db.info.get(_HANDOVER_COMMITTED_TX_KEY), (
        "AC-H24: the committed-transaction marker must be pruned on every exit path, "
        "not accumulate across confirms"
    )


# --------------------------------------------------------------------------- #
# AC-H27/AC-H28: review round 2 ruling - the drain fires ONLY at the ROOT      #
# transaction's own commit, never at any savepoint release                    #
# --------------------------------------------------------------------------- #


def test_batch_apply_two_savepoints_one_dispatch_at_root(api, monkeypatch):
    """AC-H27 (review round 2).

    Round 1's fix (AC-H21) made `_mark_handover_transaction_committed` tag the SAME
    transaction `_record_handover` names (`get_nested_transaction() or
    get_transaction()`) - which cured the single-savepoint stranding, but over-corrects
    for `planning_change_service.apply`'s REAL shape: several orders, each confirmed
    inside its OWN `db.begin_nested()` / `savepoint.commit()`, one root commit at the
    end. Every savepoint's own release now genuinely counts as "committed" for that
    savepoint, so each one's queued line fires on its OWN conclusion - the reviewer's
    probe on HEAD reports exactly that: 2 dispatches, one per savepoint, instead of one
    combined dispatch at the root. The ruling: dispatch fires ONLY when the ROOT
    transaction concludes by commit.
    """
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db
    supply = ProjectSupplyService(db)

    core_so_1 = _core_so(db, world.company_id)
    core_line_1 = _core_line(
        db, core_so_1, world.product, world.warehouse, qty_ordered="10", required_date=WAS
    )
    order_1 = _project_so(
        db, world.project, so_id=core_so_1.id, autocount_doc_no=core_so_1.so_number
    )
    line_1 = _project_line(db, order_1, line_no=1, product=world.product, core_line=core_line_1)

    core_so_2 = _core_so(db, world.company_id)
    core_line_2 = _core_line(
        db, core_so_2, world.product, world.warehouse, qty_ordered="8", required_date=WAS
    )
    order_2 = _project_so(
        db, world.project, so_id=core_so_2.id, autocount_doc_no=core_so_2.so_number
    )
    line_2 = _project_line(db, order_2, line_no=1, product=world.product, core_line=core_line_2)
    db.commit()

    savepoint_1 = db.begin_nested()
    supply.confirm(
        order_1,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line_1.id), buy_qty="10")]),
        actor_user_id=world.cs_user,
    )
    savepoint_1.commit()

    savepoint_2 = db.begin_nested()
    supply.confirm(
        order_2,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line_2.id), buy_qty="8")]),
        actor_user_id=world.cs_user,
    )
    savepoint_2.commit()

    assert _handover_calls(calls) == [], (
        "nothing may dispatch at a savepoint release, only at the root commit"
    )

    db.commit()

    matches = _handover_calls(calls)
    assert len(matches) == 1, f"expected exactly one dispatch at the root commit, got {len(matches)}"
    so_numbers = {o["so_number"] for o in matches[0]["context"]["handover"]["orders"]}
    assert so_numbers == {core_so_1.so_number, core_so_2.so_number}


def test_parent_rollback_after_savepoint_release_dispatches_nothing(api, monkeypatch):
    """AC-H28 (review round 2).

    A line recorded inside a savepoint that RELEASES must not dispatch on that release
    alone - only the ROOT's own commit fires anything (AC-H27's ruling), so a root
    rollback after the savepoint has already released must leave nothing dispatched and
    nothing pending. The sibling half is the fine-grained rollback AC-H10 already pins
    at the row level, now at the savepoint level: order A's savepoint releases, order
    B's own savepoint rolls back, and the root commits - B's rollback must discard only
    B's own queued line, not A's, and the root commit must still fire exactly once, for
    A alone.
    """
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db
    supply = ProjectSupplyService(db)

    core_so = _core_so(db, world.company_id)
    core_line = _core_line(
        db, core_so, world.product, world.warehouse, qty_ordered="10", required_date=WAS
    )
    order = _project_so(
        db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number
    )
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    savepoint = db.begin_nested()
    supply.confirm(
        order,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line.id), buy_qty="10")]),
        actor_user_id=world.cs_user,
    )
    savepoint.commit()

    db.rollback()

    assert _handover_calls(calls) == [], (
        "a released savepoint must not have dispatched before the root rolled back"
    )
    assert not db.info.get(_HANDOVER_PENDING_KEY), (
        "the pending queue must be empty once the root has rolled back"
    )
    assert not db.info.get(_HANDOVER_COMMITTED_TX_KEY), (
        "AC-H24: the committed-transaction marker must be pruned on a rollback too"
    )

    # -- sibling half: order A's savepoint releases, order B's own savepoint rolls
    # back, the root commits - exactly one dispatch, A only.
    core_so_a = _core_so(db, world.company_id)
    core_line_a = _core_line(
        db, core_so_a, world.product, world.warehouse, qty_ordered="10", required_date=WAS
    )
    order_a = _project_so(
        db, world.project, so_id=core_so_a.id, autocount_doc_no=core_so_a.so_number
    )
    line_a = _project_line(db, order_a, line_no=1, product=world.product, core_line=core_line_a)

    core_so_b = _core_so(db, world.company_id)
    core_line_b = _core_line(
        db, core_so_b, world.product, world.warehouse, qty_ordered="6", required_date=WAS
    )
    order_b = _project_so(
        db, world.project, so_id=core_so_b.id, autocount_doc_no=core_so_b.so_number
    )
    line_b = _project_line(db, order_b, line_no=1, product=world.product, core_line=core_line_b)
    db.commit()

    savepoint_a = db.begin_nested()
    supply.confirm(
        order_a,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line_a.id), buy_qty="10")]),
        actor_user_id=world.cs_user,
    )
    savepoint_a.commit()

    savepoint_b = db.begin_nested()
    supply.confirm(
        order_b,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line_b.id), buy_qty="6")]),
        actor_user_id=world.cs_user,
    )
    savepoint_b.rollback()

    db.commit()

    matches = _handover_calls(calls)
    assert len(matches) == 1, f"expected exactly one dispatch, got {len(matches)}"
    so_numbers = {o["so_number"] for o in matches[0]["context"]["handover"]["orders"]}
    assert so_numbers == {core_so_a.so_number}, (
        f"order B's rolled-back savepoint must not appear, got {so_numbers}"
    )


# --------------------------------------------------------------------------- #
# AC-H22: the AC-H20 carry rule still applies when the row's live handshake    #
# is missing (rejected, or never acknowledged)                                #
# --------------------------------------------------------------------------- #


def test_unchanged_carry_silent_when_handshake_missing(api, monkeypatch):
    """AC-H22 (review round 1, S4).

    The carry gate reads `if carried and prior_ack is not None: ...compare against
    prior_ack... else: self._record_handover(raised_row, kind="raised", ...)`. When the
    row IS carried but `_live_handshake` finds nothing live to compare against (a
    rejected row is deliberately excluded there), the gate falls through to the ELSE
    branch and prints a bare fresh ORDER regardless of whether the qty/date actually
    moved - exactly backwards from AC-H20 for this one case. Rejecting by hand-setting
    the column (not through the full reject service call, which also un-decides the
    line and would stop it being carried at all) is the same technique
    `test_order_inquiry_handshake.py` itself never needs but the coordinator's brief
    names explicitly for this seam.
    """
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)

    # -- unchanged carry, but the row's live handshake is missing (rejected). --
    fixture = _raise_two_rows(api, first_qty="10", second_qty="6")
    world.db.commit()
    fixture["second"]["row"].ack_state = ACK_REJECTED
    world.db.flush()
    world.db.commit()
    calls.clear()

    supply = ProjectSupplyService(world.db)
    supply.uncover_lines(
        fixture["order"],
        [str(fixture["first"]["line"].id)],
        actor_user_id=world.cs_user,
        reason="CS took the line back.",
    )
    world.db.commit()

    matches = _handover_calls(calls)
    assert matches, "the retire must still dispatch the handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    unchanged_carry = [l for l in lines if l["qty"] == "6"]
    assert unchanged_carry == [], (
        f"an unchanged carry must print nothing even without a live handshake, found {lines}"
    )

    # -- same missing-handshake condition, but the carry's qty DID change: exactly one
    # settled line, never a second bare ORDER. --
    fixture2 = _raise_two_rows(api, first_qty="10", second_qty="6")
    world.db.commit()
    fixture2["second"]["row"].ack_state = ACK_REJECTED
    fixture2["second"]["row"].qty = Decimal("9")
    world.db.flush()
    world.db.commit()
    calls.clear()

    ProjectSupplyService(world.db).uncover_lines(
        fixture2["order"],
        [str(fixture2["first"]["line"].id)],
        actor_user_id=world.cs_user,
        reason="CS took the line back.",
    )
    world.db.commit()

    matches2 = _handover_calls(calls)
    assert matches2, "the retire must dispatch the handover"
    lines2 = matches2[-1]["context"]["handover"]["lines"]
    changed_carry = [l for l in lines2 if l["was"] == {"qty": "9"}]
    assert len(changed_carry) == 1, (
        f"a changed carry without a live handshake must still print once with was.qty, "
        f"got {lines2}"
    )
    assert changed_carry[0]["qty"] == "6"


# --------------------------------------------------------------------------- #
# AC-H23: a named (non-carried) line re-confirmed at a new qty prints BOTH the #
# cancelled old row and the raised replacement                                #
# --------------------------------------------------------------------------- #


def test_named_changed_raised_row_settles_with_one_line(api, monkeypatch):
    """AC-R2-11 (rewrite of the old AC-H23 cancel+order expectation, `PLAN-scm-oi-
    handover-r2-undo.md` S2, owner ruling Q3 "one settled line").

    Same seam `test_order_inquiry_handshake.py::test_a_supersede_of_an_acknowledged_
    row_raises_its_replacement_acknowledged` drives: a line that is NAMED again (not
    carried) at a qty the row cannot absorb AS FAR AS today's `_settle_row_in_place`
    gate goes (its own live row is a plain `INQUIRY_RAISED` ORDER row with no links -
    not yet a cascade DRAFT). Before this lane that shape fell through to the
    supersede branch (cancel the old row, raise a fresh one, two handover lines,
    `test_named_unchanged_raised_row_settles_silently`'s own sibling before the fix).
    S2 widens the `drafted` predicate to ALSO admit exactly this shape - a single
    still-owed ORDER/ORDER_BACK row, no links, not redirected, same verb the reconfirm
    would raise - so it settles in place instead: same row id, new qty, one `settled`
    handover line (`CANCEL BALANCE 2 NOS`, not a `CANCEL BALANCE 10 NOS` + `ORDER 8`
    pair)."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)

    fixture = _raise_one_row(api, qty="10")
    world.db.commit()
    original_row_id = fixture["row"].id
    calls.clear()

    # The confirm endpoint refuses a composition that does not add up to the LINE'S
    # OWN open qty ("the line is open for 10") - a genuinely different need has to come
    # from the book moving, exactly like `_settle`'s own mutation.
    fixture["core_line"].qty_ordered = Decimal("8")
    fixture["line"].qty = Decimal("8")
    world.db.flush()
    world.db.commit()

    response = _confirm(
        client, fixture["order"].id, [_line_payload(fixture["line"].id, buy_qty="8")]
    )
    assert response.status_code == 200, response.text
    world.db.commit()

    world.db.expire_all()
    row = world.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == original_row_id).one()
    assert row.state == INQUIRY_RAISED, (
        "AC-R2-11: the widened gate settles the row in place, never cancels it"
    )
    assert row.qty == Decimal("8")
    assert row.previous_qty == Decimal("10")

    matches = _handover_calls(calls)
    assert matches, "the reconfirm must dispatch the handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    assert len(lines) == 1, (
        f"AC-R2-11: exactly one settled line, never a cancel+order pair, got {lines}"
    )
    assert lines[0]["remark"] == "CANCEL BALANCE 2 NOS"
    assert lines[0]["was"] == {"qty": "10"}
    assert lines[0]["qty"] == "8"


def test_named_unchanged_raised_row_settles_silently(api, monkeypatch):
    """AC-R2-10: a NAMED line whose only live row is a plain `raised` ORDER row with
    no links, same verb, same qty and same delivery date as the new need - the row is
    kept (same id), only its `supply_decision_id` moves to the new revision, and NO
    handover line is recorded at all (no cancel, no raise, no settle - purchasing's
    instruction did not change)."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)

    fixture = _raise_one_row(api, qty="10")
    world.db.commit()
    original_row_id = fixture["row"].id
    calls.clear()

    response = _confirm(
        client, fixture["order"].id, [_line_payload(fixture["line"].id, buy_qty="10")]
    )
    assert response.status_code == 200, response.text
    world.db.commit()

    world.db.expire_all()
    row = world.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == original_row_id).one()
    assert row.state == INQUIRY_RAISED, "AC-R2-10: the unchanged row is never cancelled"
    assert row.qty == Decimal("10")

    assert _handover_calls(calls) == [], (
        "AC-R2-10: an unchanged reconfirm of a named line must record no handover line"
    )


def test_named_verb_switch_supersedes(api, monkeypatch):
    """AC-R2-12 (verb switch): the widened gate only admits a live row whose verb
    equals the verb this confirm would raise - a plain ORDER row does NOT settle in
    place when the reconfirm's own need is now an ORDER BACK, so today's supersede
    (cancel the old row, raise the new one under its own verb) still runs."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)

    fixture = _raise_one_row(api, qty="10")
    world.db.commit()
    original_row_id = fixture["row"].id
    calls.clear()

    payload = [
        {
            **_line_payload(fixture["line"].id, buy_qty="10"),
            "order_back": True,
            "cited_document": "SPO-2026/09-0099",
        }
    ]
    response = _confirm(client, fixture["order"].id, payload)
    assert response.status_code == 200, response.text
    world.db.commit()

    world.db.expire_all()
    old_row = world.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == original_row_id).one()
    assert old_row.state == INQUIRY_CANCELLED, "AC-R2-12: a verb switch still supersedes"

    matches = _handover_calls(calls)
    assert matches, "the reconfirm must dispatch the handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    assert any(l["remark"].startswith("CANCEL BALANCE") for l in lines), lines
    assert any(l["remark"].startswith("ORDER BACK") for l in lines), lines


def test_named_two_live_rows_supersede(api, monkeypatch):
    """AC-R2-12 (two live rows): `_settle_row_in_place` already declines a line
    carrying two still-owed rows regardless of state - there is no single instruction
    to read the reconfirm's new need against - so the widened gate must not change
    this shape either: both rows are cancelled and a fresh ORDER row is raised, same
    as today."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)

    fixture = _raise_one_row(api, qty="10")
    world.db.commit()
    row_a = fixture["row"]

    row_b = OrderInquiryRow(
        company_id=world.company_id,
        order_inquiry_id=row_a.order_inquiry_id,
        so_line_id=fixture["line"].id,
        item_code=row_a.item_code,
        qty=Decimal("3"),
        delivery_date=row_a.delivery_date,
        verb=IV_ORDER,
        state=INQUIRY_RAISED,
    )
    world.db.add(row_b)
    world.db.commit()
    calls.clear()

    fixture["core_line"].qty_ordered = Decimal("8")
    fixture["line"].qty = Decimal("8")
    world.db.flush()
    world.db.commit()

    response = _confirm(
        client, fixture["order"].id, [_line_payload(fixture["line"].id, buy_qty="8")]
    )
    assert response.status_code == 200, response.text
    world.db.commit()

    world.db.expire_all()
    old_a = world.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row_a.id).one()
    old_b = world.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row_b.id).one()
    assert old_a.state == INQUIRY_CANCELLED
    assert old_b.state == INQUIRY_CANCELLED

    matches = _handover_calls(calls)
    assert matches, "the reconfirm must dispatch the handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    cancelled = [l for l in lines if l["remark"].startswith("CANCEL BALANCE")]
    assert len(cancelled) == 2, f"AC-R2-12: both still-owed rows must print cancelled, got {lines}"


# --------------------------------------------------------------------------- #
# AC-H8: purchasing's own actions never dispatch                              #
# --------------------------------------------------------------------------- #


def test_purchasing_actions_do_not_dispatch(api, monkeypatch):
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)

    # acknowledge
    ack_fixture = _raise_one_row(api, qty="10")
    world.db.commit()
    calls.clear()
    with _as_purchasing(world) as buyer:
        resp = buyer.post(ACK_URL, json={"row_ids": [str(ack_fixture["row"].id)]})
        assert resp.status_code == 200, resp.text
    world.db.commit()
    assert _handover_calls(calls) == [], "acknowledge must not dispatch the handover"

    # reject
    reject_fixture = _raise_one_row(api, qty="10")
    world.db.commit()
    calls.clear()
    with _as_purchasing(world) as buyer:
        resp = buyer.post(
            f"{LIST}/{reject_fixture['row'].id}/reject", json={"reason": "No stock"}
        )
        assert resp.status_code == 200, resp.text
    world.db.commit()
    assert _handover_calls(calls) == [], "reject must not dispatch the handover"

    # link now (drives auto_place_for_products / place_on_po underneath)
    _open_po_line(world, qty=50)
    link_fixture = _raise_one_row(api, qty="10")
    world.db.commit()
    calls.clear()
    with _as_purchasing(world) as buyer:
        resp = buyer.post(LINK_NOW, json={"product_ids": [str(world.product.id)]})
        assert resp.status_code == 200, resp.text
    world.db.commit()
    assert _handover_calls(calls) == [], "link now / place on PO must not dispatch the handover"

    # unplace
    # S3 reversal: the PO line above carries no book match, so `link_now`'s cascade
    # only SUGGESTED it (no real link to unplace) - seeded directly here, the same
    # manual-link path a buyer's own press takes, so unplace's own non-dispatch is
    # still exercised against a genuine placement.
    _po, po_line = _open_po_line(world, qty=50)
    ProjectOrderInquiryService(world.db).place_on_po_allocations(
        str(link_fixture["row"].id),
        [{"po_line_id": str(po_line.id), "qty": "10"}],
        actor_user_id=world.buyer,
    )
    world.db.commit()
    calls.clear()
    ProjectOrderInquiryService(world.db).unplace(
        str(link_fixture["row"].id), actor_user_id=world.buyer
    )
    world.db.commit()
    assert _handover_calls(calls) == [], "unplace must not dispatch the handover"


# --------------------------------------------------------------------------- #
# AC-H9: the sheet importer never dispatches                                  #
# --------------------------------------------------------------------------- #


def test_importer_apply_does_not_dispatch(monkeypatch):
    from .test_project_order_inquiry_import_migration import D_OCT, World, sheet

    _register(None)
    calls = _captured_dispatches(monkeypatch)

    with blank_session() as db:
        company_id = db.execute(
            sa.text("select id from companies where code = 'SRT'")
        ).scalar()
        with company_scope(db, frozenset({company_id})):
            w = World(db, company_id)
            order = w.order()
            w.line(order, qty_ordered="50")
            data = sheet(
                [
                    (
                        order.so_number,
                        w.product.product_code,
                        30,
                        D_OCT,
                        w.warehouse.warehouse_code,
                        "",
                    ),
                ]
            )
            result = w.apply(data)
            assert result["rows_raised"] == 1, result
            db.commit()

    assert _handover_calls(calls) == [], "the sheet importer must never dispatch the handover"


# --------------------------------------------------------------------------- #
# AC-H10: a rollback discards the queued entry                                #
# --------------------------------------------------------------------------- #


def test_rollback_discards_pending(api, monkeypatch):
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    calls.clear()

    service = ProjectOrderInquiryService(world.db)
    savepoint = world.db.begin_nested()
    service._record_handover(
        fixture["row"], kind="settled", was={"qty": Decimal("10")}, actor_user_id=world.cs_user
    )
    savepoint.rollback()

    world.db.commit()  # an unrelated commit: nothing rolled back above may still fire

    assert _handover_calls(calls) == [], "a rolled-back record must never be dispatched"


# --------------------------------------------------------------------------- #
# AC-H15: post-commit, on a fresh session, and never raises                   #
# --------------------------------------------------------------------------- #


def test_dispatch_runs_after_commit_not_before(api, monkeypatch):
    """AC-H15, first half.

    The literal "an EmailOutbox row exists after the commit; none before" cannot be
    tested honestly on this harness: the drain's fresh `SessionLocal()` is a genuinely
    separate connection (same reasoning
    `test_order_inquiry_changed_with_links_automation.py` gives for always mocking
    `dispatch_event`), so it can never see the still-uncommitted (savepoint-only) order
    this fixture just raised - letting the real dispatch run would silently produce an
    empty/broken context rather than a genuine email-outbox row. This asserts the
    equivalent, real contract instead: nothing is dispatched while the write is still
    open, and the commit fires it exactly once.
    """
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db

    core_so = _core_so(db, world.company_id)
    core_line = _core_line(
        db, core_so, world.product, world.warehouse, qty_ordered="10", required_date=WAS
    )
    order = _project_so(
        db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number
    )
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    ProjectSupplyService(db).confirm(
        order,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line.id), buy_qty="10")]),
        actor_user_id=world.cs_user,
    )
    assert _handover_calls(calls) == [], "nothing may dispatch before the write commits"

    db.commit()
    assert len(_handover_calls(calls)) == 1, "the commit must fire the dispatch exactly once"


def test_dispatch_failure_is_logged_never_raised(api, monkeypatch):
    """AC-H15, second half: a dispatch that raises must not propagate out of commit()."""
    client, world = api
    _register(world)

    def _boom(self, trigger_type, *, context, source_kind, source_id):
        raise RuntimeError("smtp is down")

    monkeypatch.setattr(
        "app.services.automation_service.AutomationService.dispatch_event", _boom
    )

    fixture = _raise_one_row(api, qty="10")  # must not raise despite the dispatch failing
    assert fixture["row"] is not None


# --------------------------------------------------------------------------- #
# AC-H17: context shape and formats                                           #
# --------------------------------------------------------------------------- #


def test_context_shape_and_formats(api, monkeypatch):
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")

    matches = _handover_calls(calls)
    assert matches
    ctx = matches[-1]["context"]
    assert set(ctx.keys()) >= {"handover", "actor", "today"}
    handover = ctx["handover"]
    for key in ("subject_scope", "verbs", "headline", "orders", "lines", "line_count", "link"):
        assert key in handover, key

    assert handover["verbs"] == ["ORDER"]
    assert handover["headline"] == "ORDER"
    assert handover["line_count"] == 1
    line = handover["lines"][0]
    assert line["delivery_date"] == WAS.strftime("%d/%m/%Y")
    assert line["qty"] == "10"
    # `build_order_inquiry_link` now takes the HEADER id, not the SO number (S3,
    # `PLAN-oi-header-list-detail.md`, AC-LK-01) - a self-comparison against the same call
    # cannot catch a wrong id being passed in, so also assert the real shape.
    header_id = str(fixture["row"].order_inquiry_id)
    assert handover["link"] == build_order_inquiry_link(header_id)
    assert handover["link"].endswith(f"/project-sales/order-inquiries/{header_id}")
    order = handover["orders"][0]
    assert order["so_number"] == fixture["core_so"].so_number


# --------------------------------------------------------------------------- #
# AC-SD-1..4 (`PLAN-oi-handover-so-date-autocount-25sep.md`): SO DATE is the   #
# AutoCount document date, not the day the CRM pulled the order                #
# --------------------------------------------------------------------------- #


def test_handover_so_date_adopted_order_uses_core_order_date(api):
    """AC-SD-1: an adopted order (`published_at` NULL by design) prints the core SO's
    own `order_date`, not the project SO's own `created_at` - the day the CRM pulled
    the order."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_so.order_date = date(2026, 9, 18)
    db.flush()
    order = _project_so(
        db, world.project, status=SO_STATUS_ADOPTED, so_id=core_so.id,
        autocount_doc_no=core_so.so_number,
    )
    order.created_at = datetime(2026, 9, 23)
    db.flush()
    assert order.published_at is None

    facts = ProjectOrderInquiryService(db)._handover_order_facts(order.id)

    assert facts["so_date"] == date(2026, 9, 18)


def test_handover_so_date_published_order_prefers_core_order_date(api):
    """AC-SD-2: a published (authored) order with `order_date` set on its core SO
    prints the core `order_date`, not `published_at`."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_so.order_date = date(2026, 9, 18)
    db.flush()
    order = _project_so(
        db, world.project, status=SO_STATUS_PUBLISHED, so_id=core_so.id,
        autocount_doc_no=core_so.so_number,
    )
    order.published_at = datetime(2026, 9, 20)
    db.flush()

    facts = ProjectOrderInquiryService(db)._handover_order_facts(order.id)

    assert facts["so_date"] == date(2026, 9, 18)


def test_handover_so_date_falls_back_when_core_order_date_is_null(api):
    """AC-SD-3: an order whose core SO has `order_date` NULL falls back to
    `published_at`, then `created_at` - today's behaviour preserved."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)  # order_date left NULL
    db.flush()
    order = _project_so(
        db, world.project, status=SO_STATUS_PUBLISHED, so_id=core_so.id,
        autocount_doc_no=core_so.so_number,
    )
    order.published_at = datetime(2026, 9, 20)
    db.flush()

    facts = ProjectOrderInquiryService(db)._handover_order_facts(order.id)
    assert facts["so_date"] == datetime(2026, 9, 20)

    order.published_at = None
    order.created_at = datetime(2026, 9, 21)
    db.flush()

    # A fresh service instance: `_handover_order_facts` memoises per instance.
    facts_no_publish = ProjectOrderInquiryService(db)._handover_order_facts(order.id)
    assert facts_no_publish["so_date"] == datetime(2026, 9, 21)


def test_handover_so_date_no_core_so_falls_back_and_never_raises(api):
    """AC-SD-4: an order with no core SO (`so_id` NULL, a draft) still prints
    `published_at or created_at`; nothing raises."""
    client, world = api
    db = world.db
    order = _project_so(db, world.project, status=SO_STATUS_DRAFT)
    order.created_at = datetime(2026, 9, 22)
    db.flush()
    assert order.so_id is None
    assert order.published_at is None

    facts = ProjectOrderInquiryService(db)._handover_order_facts(order.id)

    assert facts["so_date"] == datetime(2026, 9, 22)


# --------------------------------------------------------------------------- #
# AC-H18: actor resolution and fallback                                       #
# --------------------------------------------------------------------------- #


def test_actor_fallback_to_raised_by(api, monkeypatch):
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")

    matches = _handover_calls(calls)
    assert matches
    actor = matches[-1]["context"]["actor"]
    assert actor is not None
    assert actor["email"] == f"{world.cs_user}@zzt.test"
    calls.clear()

    row = fixture["row"]
    inquiry = (
        world.db.query(OrderInquiry).filter(OrderInquiry.id == row.order_inquiry_id).one()
    )
    inquiry.raised_by = world.buyer
    world.db.flush()

    service = ProjectOrderInquiryService(world.db)
    service._record_handover(
        row, kind="settled", was={"qty": Decimal("10")}, actor_user_id=None
    )
    world.db.commit()

    fallback = _handover_calls(calls)
    assert fallback
    actor = fallback[-1]["context"]["actor"]
    assert actor is not None
    assert actor["email"] == f"{world.buyer}@zzt.test"
    calls.clear()

    inquiry.raised_by = None
    world.db.flush()
    service._record_handover(
        row, kind="settled", was={"qty": Decimal("10")}, actor_user_id=None
    )
    world.db.commit()

    neither = _handover_calls(calls)
    assert neither
    assert neither[-1]["context"]["actor"] is None


# --------------------------------------------------------------------------- #
# AC-H11: recipients - include_actor                                          #
# --------------------------------------------------------------------------- #


def test_include_actor_adds_actor_email():
    from app.services import automation_recipients

    with blank_session() as db:
        included = automation_recipients.resolve_recipients(
            db,
            {"user_ids": [], "role_ids": [], "extra_emails": [], "include_actor": True},
            promotion_context={"actor": {"email": "raiser@example.test", "name": "Raiser"}},
        )
        assert [r["email"] for r in included].count("raiser@example.test") == 1

        excluded = automation_recipients.resolve_recipients(
            db,
            {"user_ids": [], "role_ids": [], "extra_emails": [], "include_actor": False},
            promotion_context={"actor": {"email": "raiser@example.test", "name": "Raiser"}},
        )
        assert "raiser@example.test" not in [r["email"] for r in excluded]

        no_actor = automation_recipients.resolve_recipients(
            db,
            {"user_ids": [], "role_ids": [], "extra_emails": [], "include_actor": True},
            promotion_context=None,
        )
        assert no_actor == []

        deduped = automation_recipients.resolve_recipients(
            db,
            {
                "user_ids": [],
                "role_ids": [],
                "extra_emails": ["raiser@example.test"],
                "include_actor": True,
            },
            promotion_context={"actor": {"email": "raiser@example.test", "name": "Raiser"}},
        )
        assert [r["email"] for r in deduped].count("raiser@example.test") == 1


def test_normalize_recipient_config_keeps_include_actor_as_bool():
    from app.services.automation_service import AutomationService

    normalized = AutomationService._normalize_recipient_config({"include_actor": True})
    assert normalized["include_actor"] is True

    normalized_false = AutomationService._normalize_recipient_config({"include_actor": False})
    assert normalized_false["include_actor"] is False


# --------------------------------------------------------------------------- #
# AC-H13/AC-H14: seed migration + template                                    #
# --------------------------------------------------------------------------- #


def _find_seed_migration_path() -> Path | None:
    """Locate the coder's seed migration by its OWN revision id
    (`oihe_0001_seed_handover`), not by content-sniffing its body for the template
    code and trigger type it shares with a LATER migration that also touches this
    exact template (`oihr_0001_handover_r2_layout.py`, which updates the SAME
    `order_inquiry_handover_default` row in place). CI shard 1 hit exactly this
    class of collision on the sibling `order_inquiry_undone_default` seed
    (`_find_undo_seed_migration_path`, `tests/test_board_undo_email.py`) - the old
    sniff here is spared TODAY only because the coder split `TEMPLATE_CODE =
    "order_inquiry_handover" + "_default"` in `oihr_0001` specifically to defeat a
    naive substring search; matching on the revision id makes that split
    unnecessary rather than relying on it staying in place. `None` until the
    migration exists."""
    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    matches = [
        path
        for path in versions_dir.glob("*.py")
        if _file_declares_revision(path, "oihe_0001_seed_handover")
    ]
    assert len(matches) <= 1, (
        "more than one alembic migration under alembic/versions/ declares "
        f"revision = \"oihe_0001_seed_handover\": {[p.name for p in matches]}"
    )
    return matches[0] if matches else None


def _file_declares_revision(path: Path, revision_id: str) -> bool:
    """Whether THIS file is the migration whose own `revision` equals `revision_id` -
    a bare `revision = "..."` LINE (module scope, no leading whitespace), never
    `down_revision = "..."`: that variable name ends in the very same substring
    (`revision = "..."`), so a plain `in text` check over the whole file would match
    a CHILD migration naming this one as its parent too."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return False
    target = f'revision = "{revision_id}"'
    return any(line.strip() == target for line in lines)


def _load_seed_migration():
    path = _find_seed_migration_path()
    assert path is not None, (
        "no alembic migration seeding email_templates.code="
        "'order_inquiry_handover_default' / automations.trigger_type="
        "'order_inquiry_handover' was found under alembic/versions/ - the coder must "
        "add it (PLAN-scm-oi-handover-email.md section 3.6, AC-H13). Fill in this "
        "helper's discovery (or hardcode the revision id) once that file exists if a "
        "faster lookup is wanted."
    )
    spec = importlib.util.spec_from_file_location("zzt_oihe_seed_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(module, db) -> None:
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        module.upgrade()


def _run_downgrade(module, db) -> None:
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        module.downgrade()


def test_seed_migration_idempotent():
    module = _load_seed_migration()
    with blank_session() as db:
        _run_upgrade(module, db)
        _run_upgrade(module, db)  # idempotent re-run: no duplicates

        templates = db.execute(
            sa.text(
                "SELECT count(*) FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).scalar()
        assert templates == 1

        rows = db.execute(
            sa.text(
                "SELECT enabled, group_matches, recipient_config FROM automations "
                "WHERE trigger_type = 'order_inquiry_handover' "
                "AND name = 'Order inquiry to purchasing'"
            )
        ).fetchall()
        assert len(rows) == 1
        enabled, group_matches, recipient_config = rows[0]
        assert enabled is True
        assert group_matches is False
        cfg = (
            recipient_config
            if isinstance(recipient_config, dict)
            else json.loads(recipient_config)
        )
        assert cfg.get("include_actor") is True
        assert cfg.get("one_email") is True, (
            "AC-H13/AC-H26: the seed must ask for the single combined email, or the "
            "seeded automation reverts to a copy per recipient with nobody noticing"
        )
        assert cfg.get("user_ids") == []
        assert cfg.get("extra_emails") == []
        assert cfg.get("role_ids") == [], (
            "no purchasing role exists in this blank schema, so role_ids must seed empty"
        )


def test_seed_migration_downgrade_removes_both():
    module = _load_seed_migration()
    with blank_session() as db:
        _run_upgrade(module, db)
        _run_downgrade(module, db)

        templates = db.execute(
            sa.text(
                "SELECT count(*) FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).scalar()
        assert templates == 0
        automations = db.execute(
            sa.text(
                "SELECT count(*) FROM automations WHERE trigger_type = "
                "'order_inquiry_handover'"
            )
        ).scalar()
        assert automations == 0


def test_template_renders_strike_and_text_was():
    from app.models.email_template import EmailTemplate
    from app.services.email_template_service import EmailTemplateService

    module = _load_seed_migration()
    with blank_session() as db:
        _run_upgrade(module, db)
        template = (
            db.query(EmailTemplate)
            .filter(EmailTemplate.code == "order_inquiry_handover_default")
            .one()
        )

        context = {
            "handover": {
                "subject_scope": "BRW-BB @ SO397450",
                "verbs": ["ADVANCE"],
                "headline": "ADVANCE",
                "orders": [
                    {"so_number": "SO397450", "customer": "BUIMACO", "project": "TUJU RESIDENCE"}
                ],
                "lines": [
                    {
                        "so_date": "02/04/2026",
                        "so_number": "SO397450",
                        "customer": "BUIMACO",
                        "project": "TUJU RESIDENCE",
                        "item_code": "CB6633",
                        "qty": "540",
                        "delivery_date": "21/07/2026",
                        "remark": "ADVANCE",
                        "was": {"delivery_date": "03/08/2026"},
                    }
                ],
                "line_count": 1,
                "link": "https://crm.test/project-sales/order-inquiries?query=SO397450",
            },
            "actor": {"name": "Maryam Ariffin", "email": "person32@example.com"},
            "today": "2026-09-16",
        }

        rendered = EmailTemplateService(db).render(template, context)

        assert rendered["subject"] == "OI: BRW-BB @ SO397450"
        html = rendered["body_html"]
        assert "<s>03/08/2026</s>" in html
        for header in ("S/O NO", "CUSTOMER", "PROJECT"):
            assert header in html
        for header in ("SO DATE", "ITEM CODE", "QTY", "DELIVERY DATE", "REMARK"):
            assert header in html
        assert context["handover"]["link"] in html
        assert "21/07/2026 (was 03/08/2026)" in rendered["body_text"]


def test_template_prints_blank_not_none_and_inline_borders():
    """AC-H14 extension (production-copy render finding, 16 Sep).

    A real render on the prod copy printed `PROJECT: None` in both bodies: Jinja's
    default autoescape prints Python's `None` as the literal string "None" for any of
    these fields that legitimately come back empty - no project registration, no core
    SO customer, a claim-only row with no SO date, a row that carries no `was`. Every
    one of those has to print BLANK, never the word "None".

    Email clients carry no stylesheet, so a `<table>` with no inline cell styling reads
    as a squashed grid of joined text with no borders - which the outbox preview also
    showed. On HEAD the only inline styles anywhere in the body are `border-collapse` on
    the two `<table>` elements and the red headline; every `<td>`/`<th>` carries none.
    """
    from app.models.email_template import EmailTemplate
    from app.services.email_template_service import EmailTemplateService

    module = _load_seed_migration()
    with blank_session() as db:
        _run_upgrade(module, db)
        template = (
            db.query(EmailTemplate)
            .filter(EmailTemplate.code == "order_inquiry_handover_default")
            .one()
        )

        context = {
            "handover": {
                "subject_scope": "SO397450",
                "verbs": ["ORDER"],
                "headline": "ORDER",
                "orders": [{"so_number": "SO397450", "customer": None, "project": None}],
                "lines": [
                    {
                        "so_date": None,
                        "so_number": "SO397450",
                        "customer": None,
                        "project": None,
                        "item_code": "CB6633",
                        "qty": "540",
                        "delivery_date": None,
                        "remark": "ORDER",
                        "was": None,
                    }
                ],
                "line_count": 1,
                "link": "https://crm.test/project-sales/order-inquiries?query=SO397450",
            },
            "actor": {"name": "Maryam Ariffin", "email": "person32@example.com"},
            "today": "2026-09-16",
        }

        rendered = EmailTemplateService(db).render(template, context)
        # #1349: the mail is now a whole branded document; these pins are about the
        # template's OWN cells, so they read the authored body, not the layout's table.
        from tests._email_body import authored_html

        html = authored_html(rendered["body_html"])
        text = rendered["body_text"]

        assert "None" not in html, "a blank field must print blank, not the word None"
        assert "None" not in text, "a blank field must print blank, not the word None"

        cell_tags = re.findall(r"<(?:td|th)\b[^>]*>", html)
        assert cell_tags, "the render produced no table cells at all"
        for tag in cell_tags:
            style_match = re.search(r'style="([^"]*)"', tag)
            assert style_match, f"cell carries no inline style at all: {tag}"
            style = style_match.group(1).replace(" ", "")
            assert "border:1pxsolid" in style, f"cell has no inline border: {tag}"
            assert "padding:" in style, f"cell has no inline padding: {tag}"

        header_tags = re.findall(r"<th\b[^>]*>", html)
        assert header_tags, "no header cells rendered"
        for tag in header_tags:
            style = re.search(r'style="([^"]*)"', tag).group(1).replace(" ", "")
            assert "background" in style, f"header cell carries no background colour: {tag}"

        assert " - None" not in text, "the SO summary line must not print a dash then None"


# --------------------------------------------------------------------------- #
# AC-H26: one email for the whole dispatch, the actor last (Cc)               #
# --------------------------------------------------------------------------- #


def test_handover_sends_one_email_with_actor_in_cc(monkeypatch):
    """AC-H26.

    A real send on the production copy: one confirm produced FIVE `email_outbox` rows,
    one per recipient with a single address each. R5's requirement is the manual
    mail's shape: ONE email, purchasing in To/Cc, the raiser on Cc, so reply-all
    threads across everyone. `notification_tasks.py:252-268` puts the FIRST address in
    To and the rest in Cc, so the actor has to be LAST in `recipient_emails` for
    `include_actor` to land them on Cc rather than displacing purchasing from To.

    The fix landed as an OPT-IN `recipient_config["one_email"]` key (`fcc4a5e02`) -
    other automations keep sending one copy per person, so this fixture has to ask for
    it explicitly or it exercises the (still legal) per-person path this AC is not
    about.

    Drives `AutomationService.dispatch_event` for REAL against a genuinely seeded
    `order_inquiry_handover` automation - not mocked, unlike this file's other tests -
    and reuses `test_automation_service.py`'s own assertion shape (query
    `Notification`/`NotificationDelivery` by `source_entity_type` /
    `source_entity_id`, read `data.recipient_emails` off the `Notification` row).
    """
    import uuid as _uuid

    from app.models.automation import Automation
    from app.models.email_template import EmailTemplate
    from app.models.notification import Notification, NotificationDelivery
    from app.models.user import User
    from app.services import notification_email
    from app.services.automation_service import AutomationService

    monkeypatch.setattr(notification_email, "send_notification_email", lambda *a, **kw: None)
    monkeypatch.setattr(notification_email, "send_notification_email_multi", lambda *a, **kw: None)

    with blank_session() as db:
        creator = User(
            id=str(_uuid.uuid4()),
            email=f"zzt-oihe-creator-{_uuid.uuid4().hex[:6]}@test.local",
            name="ZZT OIHE Creator",
            status="ACTIVE",
            is_trashed=False,
        )
        buyer_a = User(
            id=str(_uuid.uuid4()),
            email=f"zzt-oihe-a-{_uuid.uuid4().hex[:6]}@test.local",
            name="Purchasing A",
            status="ACTIVE",
            is_trashed=False,
        )
        buyer_b = User(
            id=str(_uuid.uuid4()),
            email=f"zzt-oihe-b-{_uuid.uuid4().hex[:6]}@test.local",
            name="Purchasing B",
            status="ACTIVE",
            is_trashed=False,
        )
        db.add_all([creator, buyer_a, buyer_b])
        db.flush()

        template = EmailTemplate(
            id=str(_uuid.uuid4()),
            code=f"zzt-oihe-tpl-{_uuid.uuid4().hex[:6]}",
            name="ZZT OIHE test template",
            subject="OI: {{ handover.subject_scope }}",
            body_html="<p>Hi {{ recipient.name }}</p>",
            body_text=None,
            is_active=True,
        )
        db.add(template)
        db.flush()

        automation = Automation(
            id=str(_uuid.uuid4()),
            name="ZZT OIHE handover",
            enabled=True,
            trigger_type="order_inquiry_handover",
            trigger_config={},
            action_type="send_email",
            email_template_id=str(template.id),
            recipient_config={
                "user_ids": [str(buyer_a.id), str(buyer_b.id)],
                "role_ids": [],
                "extra_emails": [],
                "include_actor": True,
                # Opt-in (fcc4a5e02): "one email" is a per-automation choice, not the
                # default for every automation, so this fixture has to ask for it
                # explicitly or it exercises the (still legal) per-person-copy path.
                "one_email": True,
            },
            group_matches=False,
            schedule_type="manual",
            timezone="Asia/Kuala_Lumpur",
            created_by_user_id=str(creator.id),
        )
        db.add(automation)
        db.commit()

        actor_email = f"zzt-oihe-actor-{_uuid.uuid4().hex[:6]}@test.local"
        context = {
            "handover": {
                "subject_scope": "SO397450",
                "verbs": ["ORDER"],
                "headline": "ORDER",
                "orders": [],
                "lines": [],
                "line_count": 0,
                "link": "https://crm.test/project-sales/order-inquiries?query=SO397450",
            },
            "actor": {"name": "Raiser", "email": actor_email},
            "today": "2026-09-16",
        }

        result = AutomationService(db).dispatch_event(
            "order_inquiry_handover",
            context=context,
            source_kind="order_inquiry_handover",
            source_id=str(_uuid.uuid4()),
        )
        assert result["fired"] == 1, result

        run_id = result["results"][0]["run_id"]
        assert result["results"][0]["recipients_attempted"] == 3, (
            "purchasing x2 + the actor, deduped, is what resolve_recipients names"
        )

        notifs = (
            db.query(Notification)
            .filter(
                Notification.source_entity_type == "automation_run",
                Notification.source_entity_id == run_id,
            )
            .all()
        )
        assert len(notifs) == 1, (
            f"expected exactly one email for the whole dispatch (R5), got {len(notifs)}"
        )

        deliveries = (
            db.query(NotificationDelivery)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .filter(
                Notification.source_entity_type == "automation_run",
                Notification.source_entity_id == run_id,
            )
            .all()
        )
        assert len(deliveries) == 1, (
            f"expected exactly one delivery row, no per-recipient copies, got {len(deliveries)}"
        )

        data = dict(notifs[0].data or {})
        assert data.get("single_email_to_all") is True
        recipient_emails = data.get("recipient_emails") or []
        assert len(recipient_emails) == 3, (
            f"expected purchasing x2 + the actor in one list, got {recipient_emails}"
        )
        assert {e.lower() for e in recipient_emails} == {
            buyer_a.email.lower(),
            buyer_b.email.lower(),
            actor_email.lower(),
        }
        assert recipient_emails[-1].lower() == actor_email.lower(), (
            "the actor must be LAST (notification_tasks.py puts the first address in "
            f"To and the rest in Cc), got {recipient_emails}"
        )


def test_handover_email_attachments_forwarded_via_one_email(monkeypatch):
    """#1312 fix round 1, blocker 2 (AC-E2): the automation hop was unguarded - a
    reviewer round replaced the forward with `{}` and every other test (88 of them)
    stayed green, because none of them actually drove
    `AutomationService._send_per_match`'s ONE_EMAIL branch with a context carrying
    `email_attachments`. This one does, the same real-dispatch shape
    `test_handover_sends_one_email_with_actor_in_cc` above uses (minus the
    multi-recipient bit that AC is not about), and asserts the one thing that broke:
    `Notification.data["extra_attachments"]`.
    """
    import uuid as _uuid

    from app.models.automation import Automation
    from app.models.email_template import EmailTemplate
    from app.models.notification import Notification
    from app.models.user import User
    from app.services import notification_email
    from app.services.automation_service import AutomationService

    monkeypatch.setattr(notification_email, "send_notification_email", lambda *a, **kw: None)
    monkeypatch.setattr(notification_email, "send_notification_email_multi", lambda *a, **kw: None)

    with blank_session() as db:
        creator = User(
            id=str(_uuid.uuid4()),
            email=f"zzt-oihe-ea-creator-{_uuid.uuid4().hex[:6]}@test.local",
            name="ZZT OIHE EA Creator",
            status="ACTIVE",
            is_trashed=False,
        )
        buyer = User(
            id=str(_uuid.uuid4()),
            email=f"zzt-oihe-ea-buyer-{_uuid.uuid4().hex[:6]}@test.local",
            name="Purchasing",
            status="ACTIVE",
            is_trashed=False,
        )
        db.add_all([creator, buyer])
        db.flush()

        template = EmailTemplate(
            id=str(_uuid.uuid4()),
            code=f"zzt-oihe-ea-tpl-{_uuid.uuid4().hex[:6]}",
            name="ZZT OIHE EA test template",
            subject="OI: {{ handover.subject_scope }}",
            body_html="<p>Hi {{ recipient.name }}</p>",
            body_text=None,
            is_active=True,
        )
        db.add(template)
        db.flush()

        automation = Automation(
            id=str(_uuid.uuid4()),
            name="ZZT OIHE EA handover",
            enabled=True,
            trigger_type="order_inquiry_handover",
            trigger_config={},
            action_type="send_email",
            email_template_id=str(template.id),
            recipient_config={
                "user_ids": [str(buyer.id)],
                "role_ids": [],
                "extra_emails": [],
                "include_actor": False,
                # AC-H26: the opt-in that routes this through `_send_per_match`'s
                # ONE_EMAIL branch - the ONLY branch that forwards `email_attachments`
                # (see that method's own comment).
                "one_email": True,
            },
            group_matches=False,
            schedule_type="manual",
            timezone="Asia/Kuala_Lumpur",
            created_by_user_id=str(creator.id),
        )
        db.add(automation)
        db.commit()

        extra_attachments = [
            {
                "filename": "SO423136-L3-a.png",
                "storage_provider": "r2",
                "storage_key": "k1",
                "optional": True,
            },
        ]
        context = {
            "handover": {
                "subject_scope": "SO423136",
                "verbs": ["ORDER"],
                "headline": "ORDER",
                "orders": [],
                "lines": [],
                "line_count": 0,
                "link": "https://crm.test/project-sales/order-inquiries?query=SO423136",
            },
            "actor": {"name": "Raiser", "email": "raiser@test.local"},
            "today": "2026-09-27",
            "email_attachments": extra_attachments,
        }

        result = AutomationService(db).dispatch_event(
            "order_inquiry_handover",
            context=context,
            source_kind="order_inquiry_handover",
            source_id=str(_uuid.uuid4()),
        )
        assert result["fired"] == 1, result
        run_id = result["results"][0]["run_id"]

        notif = (
            db.query(Notification)
            .filter(
                Notification.source_entity_type == "automation_run",
                Notification.source_entity_id == run_id,
            )
            .one()
        )
        data = dict(notif.data or {})
        assert data.get("extra_attachments") == extra_attachments


def test_record_handover_queues_core_line_id(api, monkeypatch):
    """#1312 fix round 1, blocker 2: `_record_handover` must queue `core_line_id` -
    the id `so_line_attachments.handover_attachments` keys attachments on - alongside
    `line_no`. Captured at `_build_handover_context`'s own INPUT (the raw pending
    queue) rather than its output: a line with no files never prints `core_line_id`
    anywhere in the built CONTEXT, so asserting against the context would not catch
    a regression here.
    """
    client, world = api
    _register(world)
    from app.services import project_order_inquiry_service as svc

    captured: list[list[dict]] = []
    real_build = svc._build_handover_context

    def _capture(pending, *args, **kwargs):
        captured.append([dict(item) for item in pending])
        return real_build(pending, *args, **kwargs)

    monkeypatch.setattr(svc, "_build_handover_context", _capture)

    fixture = _raise_one_row(api, qty="10")

    assert captured, "expected _build_handover_context to have been called"
    queued = captured[-1][0]
    assert queued.get("core_line_id") == str(fixture["core_line"].id)


# =============================================================================== #
# `PLAN-scm-oi-handover-r2-undo.md` - S1 email layout, S2 named-path equality gate #
# (S2's own new tests live above, beside the rewritten AC-H23 test), S3 subject,   #
# AC-R2-16/17 still-raised amendment rows on the next Confirm's own email.         #
# TEST-FIRST: `oihr_0001_handover_r2_layout` does not exist yet, so every test     #
# that loads it fails on the `assert path is not None` below - the right reason.  #
# =============================================================================== #


def _find_r2_migration_path() -> Path | None:
    """Locate the coder's r2 layout migration by name (PLAN section 2, S1: "named
    `oihr_0001_handover_r2_layout`"). `None` until the migration exists."""
    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    for path in versions_dir.glob("oihr_0001_handover_r2_layout*.py"):
        return path
    return None


def _load_r2_migration():
    path = _find_r2_migration_path()
    assert path is not None, (
        "no alembic migration named oihr_0001_handover_r2_layout was found under "
        "alembic/versions/ - the coder must add it (PLAN-scm-oi-handover-r2-undo.md "
        "S1, AC-R2-08), down_revision undo_0003_journal_sql_null."
    )
    spec = importlib.util.spec_from_file_location("zzt_oihr_r2_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find_r3_migration_path() -> Path | None:
    """Locate the coder's LOCATION-column migration by name (`PLAN-oi-handover-email-
    location-and-line-order-24sep.md`, issue #1166: "named `oihr_0003_location_
    column`"). `None` until the migration exists."""
    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    for path in versions_dir.glob("oihr_0003_location_column*.py"):
        return path
    return None


def _load_r3_migration():
    path = _find_r3_migration_path()
    assert path is not None, (
        "no alembic migration named oihr_0003_location_column was found under "
        "alembic/versions/ - the coder must add it (issue #1166), down_revision the "
        "current alembic head."
    )
    spec = importlib.util.spec_from_file_location("zzt_oihr_r3_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table_rows(html: str) -> list[list[str]]:
    """Every `<tr>...</tr>`'s `<td>` cell texts, tags stripped, in document order."""
    # #1349: a rendered mail wraps the template in the branded layout's own table; read
    # only the authored body so the layout's footer row is not taken for a line.
    if "<!--block:custom_text-->" in html:
        from tests._email_body import authored_html

        html = authored_html(html)
    rows = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
        if cells:
            rows.append([re.sub(r"<[^>]+>", "", c).strip() for c in cells])
    return rows


def _r2_template(db):
    """The handover template as it looks TODAY - r2's layout migration, then #1166's
    LOCATION-column migration on top (same chain a real DB runs), not r2's body alone.
    Kept this name (many tests already call it): what changed is which migrations feed
    it, not what the returned row means to a caller - "the current live template"."""
    from app.models.email_template import EmailTemplate

    _run_upgrade(_load_r2_migration(), db)
    _run_upgrade(_load_r3_migration(), db)
    return (
        db.query(EmailTemplate)
        .filter(EmailTemplate.code == "order_inquiry_handover_default")
        .one()
    )


# --------------------------------------------------------------------------- #
# AC-R2-08: the r2 migration updates the r1 template in place, idempotently,  #
# inserts on a blank DB, and downgrades back to the r1 body.                  #
# --------------------------------------------------------------------------- #


def test_r2_migration_updates_template_in_place_and_is_idempotent():
    r1 = _load_seed_migration()
    r2 = _load_r2_migration()
    with blank_session() as db:
        _run_upgrade(r1, db)
        r1_html = db.execute(
            sa.text(
                "SELECT body_html FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).scalar()
        assert "QTY CHANGE TO" not in r1_html, "sanity: r1 does not carry the new column"

        _run_upgrade(r2, db)
        row = db.execute(
            sa.text(
                "SELECT subject, body_html, body_text FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).mappings().one()
        for header in (
            "SO DATE", "S/O NO", "ITEM CODE", "QTY", "QTY CHANGE TO",
            "DELIVERY DATE", "DELIVERY DATE CHANGE TO", "REMARK",
        ):
            assert header in row["body_html"], f"{header!r} missing from the r2 body_html"
            assert header in row["body_text"], f"{header!r} missing from the r2 body_text"
        assert "<s>" not in row["body_html"], "AC-R2-09: no strike markers in r2"
        count = db.execute(
            sa.text(
                "SELECT count(*) FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).scalar()
        assert count == 1

        # Idempotent re-run: same one row, same body.
        _run_upgrade(r2, db)
        row_again = db.execute(
            sa.text(
                "SELECT body_html FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).scalar()
        assert row_again == row["body_html"]
        count_again = db.execute(
            sa.text(
                "SELECT count(*) FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).scalar()
        assert count_again == 1

        # Downgrade restores the r1 body verbatim.
        _run_downgrade(r2, db)
        restored_html = db.execute(
            sa.text(
                "SELECT body_html FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).scalar()
        assert restored_html == r1_html, "downgrade must restore the r1 body verbatim"


def test_r2_migration_inserts_when_row_absent():
    """AC-R2-08: a DB without the r1 row at all still gets the r2 shape inserted."""
    r2 = _load_r2_migration()
    with blank_session() as db:
        _run_upgrade(r2, db)
        count = db.execute(
            sa.text(
                "SELECT count(*) FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).scalar()
        assert count == 1


def test_r3_migration_downgrade_restores_r2_body_byte_for_byte():
    """Nit (reviewer pass 1): the reviewer verified this by hand (loading both modules
    and comparing `_R2_BODY_HTML`/`_R2_BODY_TEXT`/`_SUBJECT`/`_COLUMNS_IN_USE` against
    `oihr_0001_handover_r2_layout`'s own strings); pinned here so a later edit to
    `oihr_0003_location_column`'s copied-byte-for-byte r2 body cannot drift unnoticed."""
    r2 = _load_r2_migration()
    r3 = _load_r3_migration()
    with blank_session() as db:
        _run_upgrade(r2, db)
        r2_row = db.execute(
            sa.text(
                "SELECT subject, body_html, body_text FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).mappings().one()

        _run_upgrade(r3, db)
        r3_row = db.execute(
            sa.text(
                "SELECT subject, body_html, body_text FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).mappings().one()
        assert "LOCATION" in r3_row["body_html"] and "LOCATION" in r3_row["body_text"], (
            "sanity: r3 must actually add LOCATION before this test compares downgrade"
        )

        _run_downgrade(r3, db)
        restored = db.execute(
            sa.text(
                "SELECT subject, body_html, body_text FROM email_templates WHERE code = "
                "'order_inquiry_handover_default'"
            )
        ).mappings().one()
        assert restored["subject"] == r2_row["subject"]
        assert restored["body_html"] == r2_row["body_html"], (
            "r3's downgrade must restore the r2 body_html byte for byte"
        )
        assert restored["body_text"] == r2_row["body_text"], (
            "r3's downgrade must restore the r2 body_text byte for byte"
        )


# --------------------------------------------------------------------------- #
# AC-R2-01..05, 09: the r2 line table's own cells, per kind.                  #
# --------------------------------------------------------------------------- #


def _expected_r2_line_headers(was: dict | None) -> list[str]:
    """AC-R2-18: a CHANGE TO column is a property of the EMAIL, not of the line - but
    each of these test cases is a ONE-line email, so the email's own answer is exactly
    this line's own `was` keys. QTY CHANGE TO only when `was.qty` is set; DELIVERY DATE
    CHANGE TO only when `was.delivery_date` is set; neither for a plain raise.

    AC-1 (24 Sep, issue #1166): LOCATION sits right after S/O NO, unconditionally -
    unlike the two CHANGE TO columns, it is not a property of what moved.
    """
    was = was or {}
    headers = ["SO DATE", "S/O NO", "LOCATION", "ITEM CODE", "QTY"]
    if was.get("qty") is not None:
        headers.append("QTY CHANGE TO")
    headers.append("DELIVERY DATE")
    if was.get("delivery_date") is not None:
        headers.append("DELIVERY DATE CHANGE TO")
    headers.append("REMARK")
    return headers


@pytest.mark.parametrize(
    ("kind_label", "line_ctx", "expected_cells"),
    [
        (
            # AC-R2-18: QTY CHANGE TO present (`was.qty` set), DELIVERY DATE CHANGE TO
            # absent - so `expected_cells` carries no trailing "" for the absent column.
            # AC-1: a named location renders as itself.
            "settled qty (182 -> 214)",
            {
                "so_date": "01/09/2026", "so_number": "SO314594",
                "item_code": "SRTWCX8605-S-RL-PJ", "qty": "214",
                "delivery_date": "01/09/2026", "remark": "ORDER 32",
                "location": "SRT-MAIN",
                "was": {"qty": "182"},
            },
            ["01/09/2026", "SO314594", "SRT-MAIN", "SRTWCX8605-S-RL-PJ", "182", "214",
             "01/09/2026", "ORDER 32"],
        ),
        (
            # AC-R2-18: DELIVERY DATE CHANGE TO present (`was.delivery_date` set), QTY
            # CHANGE TO absent - no "" cell for the absent column.
            # AC-1: `location: None` (a row with no stock location) renders blank,
            # never the word "None".
            "settled date (01/09/2026 -> 01/04/2027)",
            {
                "so_date": "01/09/2026", "so_number": "SO314594",
                "item_code": "CB2806A", "qty": "280",
                "delivery_date": "01/04/2027", "remark": "DELAY",
                "location": None,
                "was": {"delivery_date": "01/09/2026"},
            },
            ["01/09/2026", "SO314594", "", "CB2806A", "280",
             "01/09/2026", "01/04/2027", "DELAY"],
        ),
        (
            # AC-R2-18: QTY CHANGE TO present (`was.qty` set), DELIVERY DATE CHANGE TO
            # absent. AC-1: an already-blank `location` ("", what `_record_handover`
            # itself writes for a row with no stock location) renders blank too.
            "cancelled (old qty 280)",
            {
                "so_date": "01/09/2026", "so_number": "SO314594",
                "item_code": "CB2807", "qty": "0",
                "delivery_date": "01/09/2026", "remark": "CANCEL BALANCE 280 NOS",
                "location": "",
                "was": {"qty": "280"},
            },
            ["01/09/2026", "SO314594", "", "CB2807", "280", "0",
             "01/09/2026", "CANCEL BALANCE 280 NOS"],
        ),
        (
            # AC-R2-18: a plain raise carries no `was` at all - neither column, seven
            # headers, seven cells. AC-1: no `location` key at all (a context built
            # before this lane) must still render blank, not raise or print "None".
            "plain raised",
            {
                "so_date": "01/09/2026", "so_number": "SO314594",
                "item_code": "CSH2072", "qty": "214",
                "delivery_date": "01/09/2026", "remark": "ORDER",
                "was": None,
            },
            ["01/09/2026", "SO314594", "", "CSH2072", "214",
             "01/09/2026", "ORDER"],
        ),
    ],
)
def test_handover_r2_template_cells(kind_label, line_ctx, expected_cells):
    """AC-R2-01..05, 09, 18. AC-1 (issue #1166): LOCATION column, right after S/O NO."""
    from app.services.email_template_service import EmailTemplateService

    with blank_session() as db:
        template = _r2_template(db)
        context = {
            "handover": {
                "subject_scope": "SO314594",
                "verbs": [line_ctx["remark"].split()[0]],
                "headline": line_ctx["remark"],
                "orders": [
                    {"so_number": "SO314594", "customer": "BUIMACO", "project": "TUJU RESIDENCE"}
                ],
                "lines": [line_ctx],
                "line_count": 1,
                "link": "https://crm.test/project-sales/order-inquiries?query=SO314594",
            },
            "actor": {"name": "Eling", "email": "person12@example.com"},
            "today": "18/09/2026",
        }
        rendered = EmailTemplateService(db).render(template, context)
        html = rendered["body_html"]

        assert "<s>" not in html, f"AC-R2-09 ({kind_label}): no strike markers in r2"

        headers = [
            re.sub(r"<[^>]+>", "", h).strip()
            for h in re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)
        ]
        # CUSTOMER/PROJECT belong to the SO table above only - the SO table always has
        # exactly three <th>s, so the line table's own headers are everything after them
        # (AC-R2-18: the count is no longer fixed at eight).
        line_headers = headers[3:]
        expected_headers = _expected_r2_line_headers(line_ctx["was"])
        assert line_headers == expected_headers, (
            f"AC-R2-01/18 ({kind_label}): header order/count wrong, got {line_headers}, "
            f"expected {expected_headers}"
        )
        assert "CUSTOMER" not in line_headers and "PROJECT" not in line_headers

        rows = _table_rows(html)
        line_row = rows[-1]
        assert line_row == expected_cells, (
            f"AC-R2-0x ({kind_label}): {line_row} != {expected_cells}"
        )


def test_handover_r2_template_text_body_shows_location():
    """AC-1 (reviewer pass 1, blocking K5): the plan's own test list says "LOCATION in
    both html and text", but every other cell-shape test in this file renders
    `body_html` only - `_BODY_TEXT` losing its LOCATION header or its per-line cell
    would pass every one of them. Pinned here against `body_text` directly."""
    from app.services.email_template_service import EmailTemplateService

    with blank_session() as db:
        template = _r2_template(db)
        context = {
            "handover": {
                "subject_scope": "SO314594", "verbs": ["ORDER"], "headline": "ORDER",
                "orders": [
                    {"so_number": "SO314594", "customer": "BUIMACO", "project": "TUJU RESIDENCE"}
                ],
                "lines": [
                    {
                        "so_date": "01/09/2026", "so_number": "SO314594",
                        "item_code": "CB9999", "qty": "214",
                        "delivery_date": "01/09/2026", "remark": "ORDER 214",
                        "location": "SRT-MAIN", "was": None,
                    }
                ],
                "line_count": 1,
                "link": "https://crm.test/project-sales/order-inquiries?query=SO314594",
            },
            "actor": {"name": "Eling", "email": "person12@example.com"},
            "today": "18/09/2026",
        }
        rendered = EmailTemplateService(db).render(template, context)
        text = rendered["body_text"]

        header_line = next(
            (line for line in text.splitlines() if line.startswith("SO DATE |")), None
        )
        assert header_line is not None, f"no line table header found in body_text: {text!r}"
        assert "LOCATION" in header_line, (
            f"AC-1: LOCATION header missing from body_text, got {header_line!r}"
        )

        line_row = next(
            (line for line in text.splitlines() if line.startswith("01/09/2026 |")), None
        )
        assert line_row is not None, f"no line row found in body_text: {text!r}"
        cells = [cell.strip() for cell in line_row.split("|")]
        assert "SRT-MAIN" in cells, (
            f"AC-1: LOCATION cell missing from body_text line, got {line_row!r}"
        )


# --------------------------------------------------------------------------- #
# AC-R2-18: the QTY CHANGE TO / DELIVERY DATE CHANGE TO columns are each      #
# absent (header AND every row) unless at least one line in THAT email       #
# carries the matching `was` field - a per-batch, not a per-line, decision.  #
# TEST-FIRST against the r2 template as it stands today (both columns always #
# print): every assertion below that a CHANGE TO column is ABSENT is the red #
# - the column is present unconditionally, so it fails for the right reason. #
# (Owner ruling Q5, 18 Sep.)                                                  #
# --------------------------------------------------------------------------- #

# AC-1 (issue #1166): LOCATION sits right after S/O NO in every one of these shapes -
# it is not a CHANGE TO column, so AC-R2-18's per-email visibility rule does not touch it.
_PLAIN_LINE_HEADERS = [
    "SO DATE", "S/O NO", "LOCATION", "ITEM CODE", "QTY", "DELIVERY DATE", "REMARK",
]
_QTY_CHANGE_LINE_HEADERS = [
    "SO DATE", "S/O NO", "LOCATION", "ITEM CODE", "QTY", "QTY CHANGE TO",
    "DELIVERY DATE", "REMARK",
]
_DATE_CHANGE_LINE_HEADERS = [
    "SO DATE", "S/O NO", "LOCATION", "ITEM CODE", "QTY", "DELIVERY DATE",
    "DELIVERY DATE CHANGE TO", "REMARK",
]


def _handover_context(lines: list[dict]) -> dict:
    return {
        "handover": {
            "subject_scope": "SO314594",
            "verbs": [line["remark"].split()[0] for line in lines],
            "headline": lines[0]["remark"],
            "orders": [
                {"so_number": "SO314594", "customer": "BUIMACO", "project": "TUJU RESIDENCE"}
            ],
            "lines": lines,
            "line_count": len(lines),
            "link": "https://crm.test/project-sales/order-inquiries?query=SO314594",
        },
        "actor": {"name": "Eling", "email": "person12@example.com"},
        "today": "18/09/2026",
    }


def _render_r2(db, lines: list[dict]) -> dict:
    from app.services.email_template_service import EmailTemplateService

    template = _r2_template(db)
    return EmailTemplateService(db).render(template, _handover_context(lines))


def _line_headers(html: str) -> list[str]:
    headers = [
        re.sub(r"<[^>]+>", "", h).strip()
        for h in re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)
    ]
    # CUSTOMER/PROJECT belong to the SO table above only - the SO table always has
    # exactly three <th>s (S/O NO, CUSTOMER, PROJECT), so the line table's own headers
    # are everything after those three.
    return headers[3:]


def test_handover_r2_column_visibility_no_changes_omits_both_columns():
    """AC-R2-18(a). Three plain raised lines, none carrying `was` - neither CHANGE TO
    column should appear anywhere, header or row, HTML or text."""
    lines = [
        {
            "so_date": "01/09/2026", "so_number": "SO314594", "item_code": "CSH2072",
            "qty": "214", "delivery_date": "01/09/2026", "remark": "ORDER", "was": None,
        },
        {
            "so_date": "01/09/2026", "so_number": "SO314594", "item_code": "CSH2073",
            "qty": "100", "delivery_date": "01/09/2026", "remark": "ORDER", "was": None,
        },
        {
            "so_date": "01/09/2026", "so_number": "SO314594", "item_code": "CSH2074",
            "qty": "50", "delivery_date": "01/09/2026", "remark": "ORDER", "was": None,
        },
    ]
    with blank_session() as db:
        rendered = _render_r2(db, lines)
        html, text = rendered["body_html"], rendered["body_text"]

        assert "QTY CHANGE TO" not in html, "AC-R2-18(a): QTY CHANGE TO must not appear in body_html"
        assert "QTY CHANGE TO" not in text, "AC-R2-18(a): QTY CHANGE TO must not appear in body_text"
        assert "DELIVERY DATE CHANGE TO" not in html, (
            "AC-R2-18(a): DELIVERY DATE CHANGE TO must not appear in body_html"
        )
        assert "DELIVERY DATE CHANGE TO" not in text, (
            "AC-R2-18(a): DELIVERY DATE CHANGE TO must not appear in body_text"
        )
        assert _line_headers(html) == _PLAIN_LINE_HEADERS, (
            f"AC-R2-18(a): remaining six headers must stay in order, got {_line_headers(html)}"
        )


def test_handover_r2_column_visibility_qty_change_shows_only_that_column():
    """AC-R2-18(b). One settled-qty line plus one plain line - QTY CHANGE TO must
    appear (at least one line carries `was.qty`); DELIVERY DATE CHANGE TO must not
    (no line carries `was.delivery_date`)."""
    lines = [
        {
            "so_date": "01/09/2026", "so_number": "SO314594", "item_code": "SRTWCX8605-S-RL-PJ",
            "qty": "214", "delivery_date": "01/09/2026", "remark": "ORDER 32",
            "was": {"qty": "182"},
        },
        {
            "so_date": "01/09/2026", "so_number": "SO314594", "item_code": "CSH2072",
            "qty": "50", "delivery_date": "01/09/2026", "remark": "ORDER", "was": None,
        },
    ]
    with blank_session() as db:
        rendered = _render_r2(db, lines)
        html, text = rendered["body_html"], rendered["body_text"]

        assert "QTY CHANGE TO" in html, "AC-R2-18(b): QTY CHANGE TO must appear in body_html"
        assert "QTY CHANGE TO" in text, "AC-R2-18(b): QTY CHANGE TO must appear in body_text"
        assert "DELIVERY DATE CHANGE TO" not in html, (
            "AC-R2-18(b): DELIVERY DATE CHANGE TO must not appear in body_html"
        )
        assert "DELIVERY DATE CHANGE TO" not in text, (
            "AC-R2-18(b): DELIVERY DATE CHANGE TO must not appear in body_text"
        )
        assert _line_headers(html) == _QTY_CHANGE_LINE_HEADERS, (
            f"AC-R2-18(b): headers wrong, got {_line_headers(html)}"
        )


def test_handover_r2_column_visibility_date_change_shows_only_that_column():
    """AC-R2-18(c). One settled-date line - the reverse of (b): DELIVERY DATE
    CHANGE TO must appear, QTY CHANGE TO must not."""
    lines = [
        {
            "so_date": "01/09/2026", "so_number": "SO314594", "item_code": "CB2806A",
            "qty": "280", "delivery_date": "01/04/2027", "remark": "DELAY",
            "was": {"delivery_date": "01/09/2026"},
        },
    ]
    with blank_session() as db:
        rendered = _render_r2(db, lines)
        html, text = rendered["body_html"], rendered["body_text"]

        assert "DELIVERY DATE CHANGE TO" in html, (
            "AC-R2-18(c): DELIVERY DATE CHANGE TO must appear in body_html"
        )
        assert "DELIVERY DATE CHANGE TO" in text, (
            "AC-R2-18(c): DELIVERY DATE CHANGE TO must appear in body_text"
        )
        assert "QTY CHANGE TO" not in html, "AC-R2-18(c): QTY CHANGE TO must not appear in body_html"
        assert "QTY CHANGE TO" not in text, "AC-R2-18(c): QTY CHANGE TO must not appear in body_text"
        assert _line_headers(html) == _DATE_CHANGE_LINE_HEADERS, (
            f"AC-R2-18(c): headers wrong, got {_line_headers(html)}"
        )


# --------------------------------------------------------------------------- #
# AC-R2-06: an amendment-derived DELAY/ADVANCE row carries `was.delivery_date` #
# and a bare-verb REMARK, never "DELAY - Was 2026-08-25".                     #
# --------------------------------------------------------------------------- #


def test_amendment_delay_row_carries_previous_date_in_was_and_bare_verb(api, monkeypatch):
    """AC-R2-06. Today `_write` never populates `was` for an amendment-derived raise, so
    the delta's own ISO-dated sentence (`_change_note`) lands in the REMARK column via
    `handover_remark`'s note-append instead of a structured `was`."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db

    fixture = _raise_one_row(api, qty="10")
    calls.clear()

    amendment = SOAmendment(
        company_id=world.company_id,
        project_sales_order_id=fixture["order"].id,
        from_version_kind="schedule",
        status=AMENDMENT_PUBLISHED,
        delta_json={
            "rows": [
                {
                    "row_key": "0",
                    "verb": "DELAY",
                    "so_line_id": str(fixture["line"].id),
                    "product_id": str(world.product.id),
                    "product_code": world.product.product_code,
                    "qty": "10",
                    "from_value": "2026-08-25",
                    "to_value": "2026-09-10",
                }
            ]
        },
    )
    db.add(amendment)
    db.flush()

    service = ProjectOrderInquiryService(db)
    service.derive_for_amendment(amendment, actor_user_id=world.cs_user)
    db.commit()

    matches = _handover_calls(calls)
    assert matches, "an amendment-derived raise must dispatch the handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    delay = next((l for l in lines if l["remark"].startswith("DELAY")), None)
    assert delay is not None, f"expected a DELAY line, got {lines}"
    assert delay["remark"] == "DELAY", (
        f"AC-R2-06: REMARK must be the bare verb, not {delay['remark']!r}"
    )
    assert delay["was"] == {"delivery_date": "25/08/2026"}, (
        f"AC-R2-06: was.delivery_date must be the previous date dd/mm/yyyy, "
        f"got {delay['was']!r}"
    )


# --------------------------------------------------------------------------- #
# AC-R2-07: `_change_note`'s own date is dd/mm/yyyy, matching every other      #
# date the email and the OI worklist note both print.                        #
# --------------------------------------------------------------------------- #


def test_change_note_and_email_dates_are_ddmmyyyy(api):
    from app.services.project_order_inquiry_engine import CHANGE_DATE_LATER

    client, world = api
    service = ProjectOrderInquiryService(world.db)
    note = service._change_note(
        CHANGE_DATE_LATER, {"from_value": "2026-09-01", "to_value": "2026-09-10"}
    )
    assert note == "Was 01/09/2026", f"AC-R2-07: expected dd/mm/yyyy, got {note!r}"


# --------------------------------------------------------------------------- #
# AC-R2-14/15: the subject builds from non-blank locations only.             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("locations", "expected_subject"),
    [
        (["BRW-IR", None], "BRW-IR @ SO314594"),  # AC-R2-14: one named, blanks ignored
        (["BRW-IR", "SEL", None], "SO314594"),  # AC-R2-15: two+ named stays mixed/bare
        ([None], "SO314594"),  # only blanks -> bare, unchanged
    ],
)
def test_subject_ignores_blank_locations(locations, expected_subject):
    from app.services.project_order_inquiry_service import _build_handover_context

    def _pending(location):
        return {
            "pso_id": "pso-1", "so_number": "SO314594", "customer": "BUIMACO",
            "project": "TUJU", "stock_location": location, "verb_keys": (),
            "line": {"item_code": "X"}, "order_inquiry_id": "oi-1",
            "actor": {"name": "Eling", "email": "person12@example.com"},
        }

    context, _ = _build_handover_context([_pending(loc) for loc in locations])
    assert context["handover"]["subject_scope"] == expected_subject


# --------------------------------------------------------------------------- #
# AC-R2-16/17: still-raised amendment rows ride along on the next Confirm's   #
# own email, queued once - AC-2 (24 Sep, issue #1166) then sorts the WHOLE    #
# queue by AutoCount line_no, so being a carried-forward amendment row rather #
# than the confirm's own new line no longer decides where it PRINTS.         #
# --------------------------------------------------------------------------- #


def test_confirm_email_appends_still_raised_amendment_rows_once(api, monkeypatch):
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db

    fixture = _raise_one_row(api, qty="10")
    db.commit()

    core_line_b = _core_line(
        db, fixture["core_so"], world.product, world.warehouse,
        qty_ordered="6", required_date=WAS,
    )
    line_b = _project_line(
        db, fixture["order"], line_no=2, product=world.product, core_line=core_line_b
    )
    db.commit()

    amendment = SOAmendment(
        company_id=world.company_id, project_sales_order_id=fixture["order"].id,
        from_version_kind="schedule", status=AMENDMENT_PUBLISHED,
        delta_json={
            "rows": [
                {
                    "row_key": "0", "verb": "DELAY", "so_line_id": str(fixture["line"].id),
                    "product_id": str(world.product.id),
                    "product_code": world.product.product_code,
                    "qty": "10", "from_value": "2026-09-01", "to_value": "2026-10-01",
                }
            ]
        },
    )
    db.add(amendment)
    db.flush()
    inquiry = ProjectOrderInquiryService(db).derive_for_amendment(
        amendment, actor_user_id=world.cs_user
    )
    db.commit()
    calls.clear()

    delay_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.order_inquiry_id == inquiry.id)
        .one()
    )
    assert delay_row.state == INQUIRY_RAISED, "setup: the amendment row must still be raised"

    response = _confirm(client, fixture["order"].id, [_line_payload(line_b.id, buy_qty="6")])
    assert response.status_code == 200, response.text
    db.commit()

    matches = _handover_calls(calls)
    assert matches, "the confirm must dispatch its own handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    own_lines = [l for l in lines if l["qty"] == "6" and l["remark"] == "ORDER"]
    assert own_lines, f"the confirm's own line must be present, got {lines}"

    delay_lines = [
        l for l in lines
        if l["remark"] == "DELAY" and (l.get("was") or {}).get("delivery_date")
    ]
    assert len(delay_lines) == 1, (
        f"AC-R2-16: the still-raised amendment row must ride along exactly once, got {lines}"
    )
    assert delay_lines[0]["was"]["delivery_date"] == "01/09/2026"
    # AC-2 (24 Sep, issue #1166): the amendment row names line 1, the confirm's own
    # new line is line 2 - print order follows AutoCount line_no, so the amendment row
    # (queued AFTER the confirm's own line, AC-R2-16's own append order) prints FIRST.
    assert lines.index(delay_lines[0]) < lines.index(own_lines[0]), (
        "AC-2: within one S/O, lines print in AutoCount line_no order regardless of "
        "queue order"
    )


def test_confirm_without_amendment_rows_appends_nothing(api, monkeypatch):
    """AC-R2-17: a Confirm on an order with no still-raised amendment rows appends
    nothing extra - only its own lines print."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)

    fixture = _raise_one_row(api, qty="10")
    world.db.commit()
    calls.clear()

    fixture["core_line"].qty_ordered = Decimal("8")
    fixture["line"].qty = Decimal("8")
    world.db.flush()
    world.db.commit()

    response = _confirm(
        client, fixture["order"].id, [_line_payload(fixture["line"].id, buy_qty="8")]
    )
    assert response.status_code == 200, response.text
    world.db.commit()

    matches = _handover_calls(calls)
    assert matches
    lines = matches[-1]["context"]["handover"]["lines"]
    assert all(l["remark"] != "DELAY" for l in lines), (
        f"no amendment rows exist on this order, nothing extra should print: {lines}"
    )


# --------------------------------------------------------------------------- #
# AC-R2-13: the SO314594 shape (plan section 1 numbers) - a full re-confirm    #
# naming every line prints only the genuinely NEW lines, keeping every         #
# unchanged row's own id. Scoped-down proxy: four plain raised rows (the       #
# numbers the plan and UAC give, 280/280/214/280) plus the two new lines       #
# (214/214) - the cascade-DRAFTED-with-a-link shape is the SAME "settle        #
# silently when unchanged" seam AC-R2-10 already pins at the single-row level, #
# and the three amendment DELAY rows are AC-R2-16's own fixture - building all #
# three shapes again here would not exercise anything this file does not      #
# already cover, per the tester's own time budget.                            #
# --------------------------------------------------------------------------- #


def test_so314594_shape_full_reconfirm_prints_two_lines(api, monkeypatch):
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db

    core_so = _core_so(db, world.company_id)
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)

    plain_qtys = ["280", "280", "214", "280"]
    plain_lines = []
    for i, qty in enumerate(plain_qtys, start=1):
        core_line = _core_line(
            db, core_so, world.product, world.warehouse, qty_ordered=qty, required_date=WAS
        )
        line = _project_line(db, order, line_no=i, product=world.product, core_line=core_line)
        plain_lines.append((line, qty))
    db.commit()

    first_payload = [_line_payload(line.id, buy_qty=qty) for line, qty in plain_lines]
    first = _confirm(client, order.id, first_payload)
    assert first.status_code == 200, first.text
    db.commit()

    original_row_ids = {
        line.id: (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
            .one()
            .id
        )
        for line, _qty in plain_lines
    }

    new_qtys = ["214", "214"]
    new_lines = []
    for i, qty in enumerate(new_qtys, start=len(plain_lines) + 1):
        core_line = _core_line(
            db, core_so, world.product, world.warehouse, qty_ordered=qty, required_date=WAS
        )
        line = _project_line(db, order, line_no=i, product=world.product, core_line=core_line)
        new_lines.append((line, qty))
    db.commit()
    calls.clear()

    full_payload = [_line_payload(line.id, buy_qty=qty) for line, qty in plain_lines] + [
        _line_payload(line.id, buy_qty=qty) for line, qty in new_lines
    ]
    second = _confirm(client, order.id, full_payload)
    assert second.status_code == 200, second.text
    db.commit()

    matches = _handover_calls(calls)
    assert matches, "the full re-confirm must dispatch the handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    assert len(lines) == 2, (
        f"AC-R2-13: exactly the two NEW lines must print, no cancel+order pair for "
        f"the four unchanged rows, got {lines}"
    )
    assert {l["qty"] for l in lines} == {"214"}

    db.expire_all()
    for line, _qty in plain_lines:
        row = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
            .one()
        )
        assert row.id == original_row_ids[line.id], (
            "AC-R2-13: every unchanged plain row must keep its own id across the "
            "full re-confirm"
        )
        assert row.state == INQUIRY_RAISED


# =============================================================================== #
# Review round 1 (`documentation/plans/scm/PLAN-scm-oi-handover-r2-undo.md`)       #
# S1 legacy ISO note, S4 silent reconfirm queues no amendment rows, and the        #
# "0 | 214" template cell nit.                                                    #
# =============================================================================== #


def test_amendment_delay_row_with_legacy_iso_note_prints_parsed_date_and_bare_verb(
    api, monkeypatch
):
    """S1 (review round 1): a PRE-LANE amendment row - `previous_delivery_date` NULL,
    note in the OLD ISO shape `_change_note` wrote before AC-R2-07's dd/mm/yyyy fix
    (`Was 2026-09-01`) - must still have its delivery-date move recognised when it
    rides along on the next Confirm's own email (AC-R2-16). `_amendment_row_was`'s own
    regex (`Was (\\d{2})/(\\d{2})/(\\d{4})`) only matches the NEW dd/mm/yyyy shape, so a
    legacy note falls through to `was = None` today, and `handover_remark` prints the
    whole legacy sentence back onto REMARK instead (`DELAY - Was 2026-09-01`)."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db

    fixture = _raise_one_row(api, qty="10")
    db.commit()

    core_line_b = _core_line(
        db, fixture["core_so"], world.product, world.warehouse,
        qty_ordered="6", required_date=WAS,
    )
    line_b = _project_line(
        db, fixture["order"], line_no=2, product=world.product, core_line=core_line_b
    )
    db.commit()

    amendment = SOAmendment(
        company_id=world.company_id, project_sales_order_id=fixture["order"].id,
        from_version_kind="schedule", status=AMENDMENT_PUBLISHED,
    )
    db.add(amendment)
    db.flush()
    inquiry = OrderInquiry(
        company_id=world.company_id, project_sales_order_id=fixture["order"].id,
        amendment_id=amendment.id, state=INQUIRY_RAISED, raised_by=world.cs_user,
    )
    db.add(inquiry)
    db.flush()
    legacy_row = OrderInquiryRow(
        company_id=world.company_id, order_inquiry_id=inquiry.id,
        so_line_id=fixture["line"].id, item_code=world.product.product_code,
        qty=Decimal("10"), verb=IV_DELAY, state=INQUIRY_RAISED,
        # PRE-LANE shape: no `previous_delivery_date` write, and the note in the
        # ISO format the OLD `_change_note` wrote, before AC-R2-07 landed.
        note="Was 2026-09-01",
    )
    db.add(legacy_row)
    db.commit()
    calls.clear()

    response = _confirm(client, fixture["order"].id, [_line_payload(line_b.id, buy_qty="6")])
    assert response.status_code == 200, response.text
    db.commit()

    matches = _handover_calls(calls)
    assert matches, "the confirm must dispatch its own handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    delay_lines = [l for l in lines if l["remark"] == "DELAY"]
    assert delay_lines, (
        f"S1: expected a bare DELAY line even for a legacy ISO note, got {lines}"
    )
    assert delay_lines[0]["was"] == {"delivery_date": "01/09/2026"}, (
        f"S1: a legacy ISO note must still parse to dd/mm/yyyy, got "
        f"{delay_lines[0]['was']!r}"
    )


def test_silent_reconfirm_queues_no_amendment_rows_and_no_email(api, monkeypatch):
    """S4 (captain ruling, review round 1): a re-confirm that settles every named
    line silently (AC-R2-10 shape - no line of its OWN reaches the handover queue)
    must not append the order's still-raised amendment rows either, and must
    dispatch NO handover email at all. Today `_append_still_raised_amendment_rows`
    runs unconditionally at the end of `refresh_for_decision`, so a still-raised
    amendment row rides along on a confirm that otherwise said nothing of its own -
    purchasing gets an email whose only line is one they already saw on the
    amendment's own publish email.

    The companion half of this ruling ("a confirm with one own line still appends
    them") is already pinned by `test_confirm_email_appends_still_raised_amendment_
    rows_once` above - not duplicated here.
    """
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db

    fixture = _raise_one_row(api, qty="10")
    db.commit()

    amendment = SOAmendment(
        company_id=world.company_id, project_sales_order_id=fixture["order"].id,
        from_version_kind="schedule", status=AMENDMENT_PUBLISHED,
        delta_json={
            "rows": [
                {
                    "row_key": "0", "verb": "DELAY", "so_line_id": str(fixture["line"].id),
                    "product_id": str(world.product.id),
                    "product_code": world.product.product_code,
                    "qty": "10", "from_value": "2026-09-01", "to_value": "2026-10-01",
                }
            ]
        },
    )
    db.add(amendment)
    db.flush()
    ProjectOrderInquiryService(db).derive_for_amendment(amendment, actor_user_id=world.cs_user)
    db.commit()
    calls.clear()

    # Silent re-confirm: the SAME line, the SAME qty - AC-R2-10's widened gate settles
    # it with no handover line of its own.
    response = _confirm(
        client, fixture["order"].id, [_line_payload(fixture["line"].id, buy_qty="10")]
    )
    assert response.status_code == 200, response.text
    db.commit()

    assert _handover_calls(calls) == [], (
        "S4: a confirm with no line of its own must dispatch no handover email, "
        "even when the order carries a still-raised amendment row"
    )


def test_handover_r2_template_cell_was_qty_zero_prints_0_and_change_to():
    """Nit (review round 1): `was.qty == "0"` (a row previously at zero, now raised)
    must print `QTY = 0`, `QTY CHANGE TO = 214` - the string `"0"` is non-empty and
    therefore truthy in Jinja, so this is a defence against a future regression
    (`_qty_str`/the template's own `{% if line.was.qty %}` check), not a currently
    broken seam."""
    from app.services.email_template_service import EmailTemplateService

    with blank_session() as db:
        template = _r2_template(db)
        line_ctx = {
            "so_date": "01/09/2026", "so_number": "SO314594", "item_code": "CB9999",
            "qty": "214", "delivery_date": "01/09/2026", "remark": "ORDER 214",
            "was": {"qty": "0"},
        }
        context = {
            "handover": {
                "subject_scope": "SO314594", "verbs": ["ORDER"], "headline": "ORDER",
                "orders": [{"so_number": "SO314594", "customer": "BUIMACO", "project": "TUJU"}],
                "lines": [line_ctx], "line_count": 1,
                "link": "https://crm.test/project-sales/order-inquiries?query=SO314594",
            },
            "actor": {"name": "Eling", "email": "person12@example.com"},
            "today": "18/09/2026",
        }
        rendered = EmailTemplateService(db).render(template, context)
        rows = _table_rows(rendered["body_html"])
        line_row = rows[-1]
        # AC-R2-18: DELIVERY DATE CHANGE TO is absent (no line carries `was.delivery_
        # date`), so there is no trailing "" cell for it. AC-1: `line_ctx` above carries
        # no `location` key at all, so LOCATION prints blank.
        assert line_row == [
            "01/09/2026", "SO314594", "", "CB9999", "0", "214",
            "01/09/2026", "ORDER 214",
        ], f"nit: was.qty='0' must print '0 | 214', got {line_row}"


# =============================================================================== #
# `PLAN-oi-handover-email-location-and-line-order-24sep.md` (issue #1166, owner    #
# ruling 24 Sep): AC-1 the stock location per line, AC-2/AC-3 the AutoCount SO     #
# line sequence sort.                                                              #
# =============================================================================== #


def test_record_handover_carries_row_location_blank_when_none(api, monkeypatch):
    """AC-1: `_record_handover` copies `row.stock_location` onto the per-line dict's
    `location` key - a value when the row carries one, blank string (never the word
    "None") when it does not."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    row = fixture["row"]
    # A row with no location, set explicitly rather than assumed off the raise - the
    # real confirm flow already stamps `world.warehouse`'s own code onto it (D17).
    row.stock_location = None
    world.db.flush()
    calls.clear()

    service = ProjectOrderInquiryService(world.db)
    service._record_handover(row, kind="settled", was={"qty": Decimal("10")})
    world.db.commit()

    matches = _handover_calls(calls)
    assert matches
    line = matches[-1]["context"]["handover"]["lines"][0]
    assert line["location"] == "", (
        f"AC-1: a row with no stock_location must print blank, never None, got {line['location']!r}"
    )
    calls.clear()

    row.stock_location = "SRT-MAIN"
    world.db.flush()
    service._record_handover(row, kind="settled", was={"qty": Decimal("10")})
    world.db.commit()

    matches = _handover_calls(calls)
    assert matches
    line = matches[-1]["context"]["handover"]["lines"][0]
    assert line["location"] == "SRT-MAIN"


def test_confirm_payload_order_is_ignored_lines_print_in_autocount_line_no_order(
    api, monkeypatch
):
    """AC-2: a three-line order confirmed with the payload naming line 3, then line 1,
    then line 2 must still print lines 1, 2, 3 - the order CS ticked them in has no
    effect on the printed order."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db

    core_so = _core_so(db, world.company_id)
    product_1, product_2, product_3 = _product(db), _product(db), _product(db)
    core_line_1 = _core_line(
        db, core_so, product_1, world.warehouse, qty_ordered="10", required_date=WAS
    )
    core_line_2 = _core_line(
        db, core_so, product_2, world.warehouse, qty_ordered="6", required_date=WAS
    )
    core_line_3 = _core_line(
        db, core_so, product_3, world.warehouse, qty_ordered="4", required_date=WAS
    )
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line_1 = _project_line(db, order, line_no=1, product=product_1, core_line=core_line_1)
    line_2 = _project_line(db, order, line_no=2, product=product_2, core_line=core_line_2)
    line_3 = _project_line(db, order, line_no=3, product=product_3, core_line=core_line_3)
    db.commit()

    # Payload order: line 3, line 1, line 2 - deliberately not the AutoCount order.
    payload = [
        _line_payload(line_3.id, buy_qty="4"),
        _line_payload(line_1.id, buy_qty="10"),
        _line_payload(line_2.id, buy_qty="6"),
    ]
    response = _confirm(client, order.id, payload)
    assert response.status_code == 200, response.text
    db.commit()

    matches = _handover_calls(calls)
    assert matches
    lines = matches[-1]["context"]["handover"]["lines"]
    item_codes = [l["item_code"] for l in lines]
    assert item_codes == [
        product_1.product_code, product_2.product_code, product_3.product_code,
    ], f"AC-2: lines must print in AutoCount line_no order, got {item_codes}"


def test_two_orders_confirmed_together_group_by_so_number_each_in_line_no_order(
    api, monkeypatch
):
    """AC-2: two orders confirmed inside the same commit print grouped by S/O no
    (ascending), each group in its own AutoCount line_no order - regardless of which
    order was confirmed first or which line inside it was named first."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db
    supply = ProjectSupplyService(db)

    # N1 (reviewer round 2): fixed, ordering-discriminating S/O numbers - queued in
    # REVERSE S/O order (B confirmed first, A confirmed second) - so the summary
    # table's own sort has something to prove, the same reasoning the AC-3 test's
    # fixed item codes already carry.
    core_so_a = _core_so(db, world.company_id)
    core_so_a.so_number = f"ZZT-A-{_uid()[:8]}"
    db.flush()
    product_a1, product_a2 = _product(db), _product(db)
    core_line_a1 = _core_line(
        db, core_so_a, product_a1, world.warehouse, qty_ordered="5", required_date=WAS
    )
    core_line_a2 = _core_line(
        db, core_so_a, product_a2, world.warehouse, qty_ordered="3", required_date=WAS
    )
    order_a = _project_so(
        db, world.project, so_id=core_so_a.id, autocount_doc_no=core_so_a.so_number
    )
    line_a1 = _project_line(db, order_a, line_no=1, product=product_a1, core_line=core_line_a1)
    line_a2 = _project_line(db, order_a, line_no=2, product=product_a2, core_line=core_line_a2)

    core_so_b = _core_so(db, world.company_id)
    core_so_b.so_number = f"ZZT-B-{_uid()[:8]}"
    db.flush()
    product_b1, product_b2 = _product(db), _product(db)
    core_line_b1 = _core_line(
        db, core_so_b, product_b1, world.warehouse, qty_ordered="7", required_date=WAS
    )
    core_line_b2 = _core_line(
        db, core_so_b, product_b2, world.warehouse, qty_ordered="2", required_date=WAS
    )
    order_b = _project_so(
        db, world.project, so_id=core_so_b.id, autocount_doc_no=core_so_b.so_number
    )
    line_b1 = _project_line(db, order_b, line_no=1, product=product_b1, core_line=core_line_b1)
    line_b2 = _project_line(db, order_b, line_no=2, product=product_b2, core_line=core_line_b2)
    db.commit()

    # Order B confirmed FIRST, its own lines scrambled; order A confirmed SECOND, its
    # own lines scrambled too - one commit, one dispatch.
    supply.confirm(
        order_b,
        ConfirmSupplyBody(lines=[
            ConfirmLine(project_line_id=str(line_b2.id), buy_qty="2"),
            ConfirmLine(project_line_id=str(line_b1.id), buy_qty="7"),
        ]),
        actor_user_id=world.cs_user,
    )
    supply.confirm(
        order_a,
        ConfirmSupplyBody(lines=[
            ConfirmLine(project_line_id=str(line_a2.id), buy_qty="3"),
            ConfirmLine(project_line_id=str(line_a1.id), buy_qty="5"),
        ]),
        actor_user_id=world.cs_user,
    )
    db.commit()

    matches = _handover_calls(calls)
    assert matches
    lines = matches[-1]["context"]["handover"]["lines"]
    assert len(lines) == 4

    so_number_by_item_code = {
        product_a1.product_code: core_so_a.so_number,
        product_a2.product_code: core_so_a.so_number,
        product_b1.product_code: core_so_b.so_number,
        product_b2.product_code: core_so_b.so_number,
    }
    line_no_by_item_code = {
        product_a1.product_code: 1, product_a2.product_code: 2,
        product_b1.product_code: 1, product_b2.product_code: 2,
    }
    printed_so_numbers = [so_number_by_item_code[l["item_code"]] for l in lines]

    # Contiguous, ascending S/O groups (never interleaved).
    so_groups: list[str] = []
    for so_number in printed_so_numbers:
        if not so_groups or so_groups[-1] != so_number:
            so_groups.append(so_number)
    assert so_groups == sorted({core_so_a.so_number, core_so_b.so_number}), (
        f"AC-2: S/O groups must be contiguous and ascending, got {printed_so_numbers}"
    )

    # Within each group, line_no ascending.
    for so_number in so_groups:
        group_line_nos = [
            line_no_by_item_code[l["item_code"]]
            for l in lines if so_number_by_item_code[l["item_code"]] == so_number
        ]
        assert group_line_nos == sorted(group_line_nos), (
            f"AC-2: within S/O {so_number}, lines must be in line_no order, got {group_line_nos}"
        )

    # N1 (reviewer round 2): the SO summary table agrees with the line table above -
    # confirmed in REVERSE S/O order (B, then A), so a dropped sort would print the
    # summary as queued (B, A) instead of ascending (A, B).
    summary_so_numbers = [o["so_number"] for o in matches[-1]["context"]["handover"]["orders"]]
    assert summary_so_numbers == sorted([core_so_a.so_number, core_so_b.so_number]), (
        f"AC-2 nit: the SO summary table must be sorted by S/O no like the line table, "
        f"got {summary_so_numbers}"
    )


def test_amendment_row_without_so_line_id_sorts_after_rows_with_one(api, monkeypatch):
    """AC-3: a still-raised amendment row that names no line (no `so_line_id`) prints
    AFTER every row of its own S/O that has one, in item code order among themselves."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db

    fixture = _raise_one_row(api, qty="10")
    db.commit()

    core_line_b = _core_line(
        db, fixture["core_so"], world.product, world.warehouse,
        qty_ordered="6", required_date=WAS,
    )
    line_b = _project_line(
        db, fixture["order"], line_no=2, product=world.product, core_line=core_line_b
    )
    db.commit()

    # Should fix 1 (reviewer pass 1): fixed, ordering-discriminating codes, not
    # `_product`'s own random `ZZT-<uuid>` ones - a random pair only fails this test
    # when the coin flip happens to land against it (measured: K1's mutation, dropping
    # the sort entirely, went red only 2 of 6 runs). Queued in the OPPOSITE of
    # alphabetical order (B first, at row_key "0"; A second, at row_key "1") so queue
    # order and item-code order can never coincide by chance - only a correct sort
    # can produce A before B here.
    other_product_b, other_product_a = _product(db), _product(db)
    other_product_b.product_code = f"ZZT-B-{_uid()[:8]}"
    other_product_a.product_code = f"ZZT-A-{_uid()[:8]}"
    db.flush()
    amendment = SOAmendment(
        company_id=world.company_id, project_sales_order_id=fixture["order"].id,
        from_version_kind="schedule", status=AMENDMENT_PUBLISHED,
        delta_json={
            "rows": [
                {
                    # No "so_line_id" at all: the product this DELAY names is not one
                    # of the order's own lines (AC-3's own case).
                    "row_key": "0", "verb": "DELAY",
                    "product_id": str(other_product_b.id),
                    "product_code": other_product_b.product_code,
                    "qty": "5", "from_value": "2026-09-01", "to_value": "2026-10-01",
                },
                {
                    "row_key": "1", "verb": "DELAY",
                    "product_id": str(other_product_a.id),
                    "product_code": other_product_a.product_code,
                    "qty": "3", "from_value": "2026-09-01", "to_value": "2026-10-01",
                },
            ]
        },
    )
    db.add(amendment)
    db.flush()
    ProjectOrderInquiryService(db).derive_for_amendment(amendment, actor_user_id=world.cs_user)
    db.commit()
    calls.clear()

    response = _confirm(client, fixture["order"].id, [_line_payload(line_b.id, buy_qty="6")])
    assert response.status_code == 200, response.text
    db.commit()

    matches = _handover_calls(calls)
    assert matches
    lines = matches[-1]["context"]["handover"]["lines"]
    item_codes = [l["item_code"] for l in lines]

    with_line_no = item_codes[:1]
    without_line_no = item_codes[1:]
    assert with_line_no == [world.product.product_code], (
        f"AC-3: the row that has a so_line_id must print first, got {item_codes}"
    )
    # Deterministic expectation, A before B - a dropped item-code tiebreak would print
    # the queue's own B-then-A order instead.
    assert without_line_no == [other_product_a.product_code, other_product_b.product_code], (
        f"AC-3: rows with no so_line_id must sort after, in item code order, "
        f"got {item_codes}"
    )
