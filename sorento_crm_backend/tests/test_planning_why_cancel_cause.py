"""PLANNING-WHY R4: every cancel of an order inquiry row records its cause.

Contract (documentation/plans/scm/planning-why-12oct-acceptance-criteria.md):
AC-W8 each code path that sets `order_inquiry_rows.state` to cancelled writes, in the same
      transaction, one audit_logs row: action CANCEL, entity_id = the row, user_id = actor or
      NULL, root_entity_type project_sales_orders / root_entity_id = the order, and
      new_values {revision_no, supply_decision_id, cause, qty, item_code, so_line_no}.
      Causes: undecided_change, superseded, book_left_nothing, cancel_so,
      planning_change_retired, reserve_moved, manual, borrow_retired.
AC-W9 a guard fails when a service assigns the cancelled state to an OI row with no
      `_record_cancel(` call in the same function.

Open point for the coder: the UAC names entity_type "order_inquiry_rows" while the ORM audit
type of the model is "project_order_inquiry_rows"; the tests accept either spelling.
Real-DB fixture chain of tests/test_planning_change_apply_on_board.py.
"""
from __future__ import annotations

import ast
import re
from decimal import Decimal
from pathlib import Path

from app.models.audit import AuditLog
from app.models.planning_change import PlanningChangeRow
from app.models.project_so import (
    INQUIRY_CANCELLED,
    OrderInquiryRow,
    SOSupplyDecision,
)
from app.services.project_order_inquiry_service import ProjectOrderInquiryService
from app.services.scm.outstanding_diff import QTY_CHANGED

from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures
    WAS_1,
    _apply_from_board,
    _build,
    _change,
    _confirm,
    _core_line,
    _core_so,
    _form_three,
    _line_payload,
    _project_line,
    _project_so,
    _rows_of,
    api,
    world,
)

ROW_TYPES = {"order_inquiry_rows", "project_order_inquiry_rows"}

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "aud_0001_audit_standard_s0.py"
)


def allowed_audit_actions() -> set[str]:
    """The actions prod's CHECK audit_logs_action_check accepts (`_NEW_ALLOWED`), read from
    the migration: the test schema is built from the ORM models and has no such constraint."""
    text = MIGRATION.read_text()
    found = re.search(r'^_NEW_ALLOWED = "\((.*?)\)"', text, re.M)
    assert found, "_NEW_ALLOWED moved in the audit migration"
    return set(re.findall(r"'([A-Z_]+)'", found.group(1)))


def _cancel_rows(world, row):
    world.db.expire_all()
    return (
        world.db.query(AuditLog)
        .filter(AuditLog.event == "OI_ROW_CANCELLED", AuditLog.entity_id == str(row.id))
        .all()
    )


def _one_cancel(world, row):
    found = _cancel_rows(world, row)
    assert len(found) == 1, f"expected one CANCEL audit row for {row.id}, got {len(found)}"
    entry = found[0]
    assert entry.action == "EVENT"
    assert entry.event == "OI_ROW_CANCELLED"
    assert entry.action in allowed_audit_actions(), "prod's audit_logs_action_check refuses it"
    assert entry.entity_type in ROW_TYPES
    return entry


def _confirmed_order(api, *, line_no=592):
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="25",
                      required_date=WAS_1)
    order = _project_so(db, world.project, so_id=core_so.id,
                        autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=line_no, product=world.product, core_line=core)
    db.commit()
    assert _confirm(client, order.id, [_line_payload(line.id, buy_qty="25")]).status_code == 200
    db.commit()
    row = _rows_of(world, line)[0]
    return {"client": client, "world": world, "order": order, "core": core, "line": line,
            "core_so": core_so, "row": row}


def _with_two_owed_rows(fx):
    """A second still-owed ORDER row on the line: the shape a settle-in-place declines, so the
    next revision must SUPERSEDE rather than restate."""
    db = fx["world"].db
    first = fx["row"]
    twin = OrderInquiryRow(
        order_inquiry_id=first.order_inquiry_id, so_line_id=first.so_line_id,
        item_code=first.item_code, qty=first.qty, delivery_date=first.delivery_date,
        stock_location=first.stock_location, verb=first.verb, state="raised",
        company_id=first.company_id, supply_decision_id=first.supply_decision_id,
    )
    db.add(twin)
    db.commit()
    fx["twin"] = twin
    return fx


def _reconfirm_at(fx, qty="20"):
    db = fx["world"].db
    fx["core"].qty_ordered = Decimal(qty)
    fx["line"].qty = Decimal(qty)
    db.commit()
    response = _confirm(fx["client"], fx["order"].id, [_line_payload(fx["line"].id, buy_qty=qty)])
    assert response.status_code == 200, response.text
    db.commit()


def _revision_two_id(fx):
    return str(
        fx["world"].db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == fx["order"].id,
                SOSupplyDecision.revision_no == 2)
        .one().id
    )


# --------------------------------------------------------------------------- supersede


def test_a_plain_revision_supersede_records_cause_superseded(api):
    fx = _with_two_owed_rows(_confirmed_order(api))
    _reconfirm_at(fx)

    for row in (fx["row"], fx["twin"]):
        assert row.state == INQUIRY_CANCELLED
        entry = _one_cancel(fx["world"], row)
        assert entry.new_values == {
            "revision_no": 2,
            "supply_decision_id": _revision_two_id(fx),
            "cause": "superseded",
            "qty": 25,
            "item_code": row.item_code,
            "so_line_no": 592,
        }
        assert str(entry.user_id) == str(fx["world"].actor)
        assert entry.root_entity_type == "project_sales_orders"
        assert str(entry.root_entity_id) == str(fx["order"].id)


def test_a_supersede_of_a_line_with_an_undecided_change_records_undecided_change(api):
    fx = _with_two_owed_rows(_confirmed_order(api))
    world = fx["world"]
    # The book moved the line and CS has not decided it: a pending row, decision NULL.
    fx["core"].qty_ordered = Decimal("20")
    fx["line"].qty = Decimal("20")
    world.db.commit()
    batch = _build(
        world,
        [_change(QTY_CHANGED, fx["core"], so_number=fx["core_so"].so_number, old_date=WAS_1,
                 new_date=WAS_1, old_qty="25", new_qty="20")],
        fx["core_so"], [str(fx["core"].id)],
    )
    pending = world.db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch.id).one()
    assert pending.decision is None and pending.applied_state == "pending"

    response = _confirm(fx["client"], fx["order"].id, [_line_payload(fx["line"].id, buy_qty="20")])
    assert response.status_code == 200, response.text
    world.db.commit()

    entry = _one_cancel(world, fx["row"])
    assert entry.new_values["cause"] == "undecided_change"
    assert entry.new_values["revision_no"] == 2


def test_a_supersede_over_an_overtaken_undecided_change_reads_superseded(api):
    """#1599's rule: an undecided row held against a revision older than the one before the
    latest can never be decided, so it holds nothing back and the cause stays `superseded`."""
    fx = _with_two_owed_rows(_confirmed_order(api))
    world = fx["world"]
    fx["core"].qty_ordered = Decimal("20")
    fx["line"].qty = Decimal("20")
    world.db.commit()
    batch = _build(
        world,
        [_change(QTY_CHANGED, fx["core"], so_number=fx["core_so"].so_number, old_date=WAS_1,
                 new_date=WAS_1, old_qty="25", new_qty="20")],
        fx["core_so"], [str(fx["core"].id)],
    )
    pending = world.db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch.id).one()
    assert pending.decision is None and pending.applied_state == "pending"
    pending.held_json = {**(pending.held_json or {}), "revision_no": 999}
    world.db.commit()

    response = _confirm(fx["client"], fx["order"].id, [_line_payload(fx["line"].id, buy_qty="20")])
    assert response.status_code == 200, response.text
    world.db.commit()

    entry = _one_cancel(world, fx["row"])
    assert entry.new_values["cause"] == "superseded"


# --------------------------------------------------------------------------- manual


def test_a_manual_cancel_records_cause_manual_with_the_user(api):
    fx = _confirmed_order(api)
    world = fx["world"]

    ProjectOrderInquiryService(world.db).mark_rows(
        [str(fx["row"].id)], state="cancelled", actor_user_id=str(world.actor)
    )
    world.db.commit()

    entry = _one_cancel(world, fx["row"])
    assert entry.new_values["cause"] == "manual"
    assert entry.new_values["revision_no"] is None
    assert entry.new_values["qty"] == 25
    assert entry.new_values["so_line_no"] == 592
    assert str(entry.user_id) == str(world.actor)
    assert str(entry.root_entity_id) == str(fx["order"].id)


def test_marking_a_row_actioned_writes_no_cancel_row(api):
    fx = _confirmed_order(api)
    world = fx["world"]

    ProjectOrderInquiryService(world.db).mark_rows(
        [str(fx["row"].id)], state="actioned", actor_user_id=str(world.actor)
    )
    world.db.commit()

    assert _cancel_rows(world, fx["row"]) == []


# --------------------------------------------------------------------------- cancel SO


def test_transfer_to_cancel_so_records_cause_cancel_so(api):
    fx = _confirmed_order(api)
    world = fx["world"]

    count = ProjectOrderInquiryService(world.db).retire_rows_for_cancelled_lines(
        [(fx["core"].id, "Customer withdrew")], actor_user_id=str(world.actor)
    )
    world.db.commit()

    assert count == 1
    entry = _one_cancel(world, fx["row"])
    assert entry.new_values["cause"] == "cancel_so"
    assert entry.new_values["item_code"] == fx["row"].item_code
    assert str(entry.user_id) == str(world.actor)


# --------------------------------------------------------------------------- planning change


def test_a_planning_change_that_retires_a_closed_lines_rows_records_the_cause(api):
    fixture = _form_three(api)
    world = fixture["world"]

    response = _apply_from_board(fixture)
    assert response.status_code == 200, response.text
    world.db.commit()

    for closed in fixture["lines"][1:]:
        rows = _rows_of(world, closed)
        assert rows and all(r.state == INQUIRY_CANCELLED for r in rows)
        for row in rows:
            entry = _one_cancel(world, row)
            assert entry.new_values["cause"] == "planning_change_retired"
            assert str(entry.root_entity_id) == str(fixture["order"].id)


# --------------------------------------------------------------------------- AC-W9 guard

SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"
# The header model (`OrderInquiry`) also carries a state with the same constant; its writer
# names the object `inquiry`, a row writer never does.
HEADER_TARGETS = {"inquiry"}
# `mark_rows` assigns a parameter, not the constant, so the literal scan cannot see it.
NAMED_WRITERS = {"mark_rows"}


def _calls_record_cancel(function: ast.AST) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
            if name == "_record_cancel":
                return True
    return False


def _assigns_cancelled_to_a_row(node: ast.AST) -> bool:
    if not isinstance(node, ast.Assign):
        return False
    value = node.value
    is_cancelled = (isinstance(value, ast.Name) and value.id == "INQUIRY_CANCELLED") or (
        isinstance(value, ast.Constant) and value.value == "cancelled"
    )
    if not is_cancelled:
        return False
    for target in node.targets:
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "state"
            and isinstance(target.value, ast.Name)
            and target.value.id not in HEADER_TARGETS
        ):
            return True
    return False


def _offenders() -> list[str]:
    found: list[str] = []
    seen_named: set[str] = set()
    for path in sorted(SERVICES.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            writes = any(_assigns_cancelled_to_a_row(n) for n in ast.walk(function))
            if function.name in NAMED_WRITERS:
                seen_named.add(function.name)
                writes = True
            if writes and not _calls_record_cancel(function):
                found.append(f"{path.relative_to(SERVICES.parent.parent)}::{function.name}")
    assert seen_named == NAMED_WRITERS, "the named writer moved; update NAMED_WRITERS"
    return found


def test_no_service_cancels_an_inquiry_row_without_recording_the_cause():
    offenders = _offenders()
    assert offenders == [], (
        "these functions set an order inquiry row to cancelled with no _record_cancel( call: "
        + ", ".join(offenders)
    )


def test_the_audit_migration_allows_event_but_not_cancel():
    allowed = allowed_audit_actions()
    assert "EVENT" in allowed
    assert "CANCEL" not in allowed and "CONFIRM_REFUSED" not in allowed


# --------------------------------------------------------------------------- AC-W9, per assignment

CANCEL_WINDOW = 4


def unrecorded_cancel_assignments(source: str) -> list[tuple[str, int]]:
    """Every `<x>.state = <cancelled>` (x not the header) that has no `_record_cancel(` call in
    the next CANCEL_WINDOW statements of the SAME block. Returns (function, line) pairs."""
    tree = ast.parse(source)
    found: list[tuple[str, int]] = []

    def has_record(stmt: ast.AST) -> bool:
        return any(
            isinstance(n, ast.Call)
            and (
                getattr(n.func, "attr", None) == "_record_cancel"
                or getattr(n.func, "id", None) == "_record_cancel"
            )
            for n in ast.walk(stmt)
        )

    def visit_block(name: str, block: list) -> None:
        for index, stmt in enumerate(block):
            if _assigns_cancelled_to_a_row(stmt) and not any(
                has_record(after) for after in block[index + 1 : index + 1 + CANCEL_WINDOW]
            ):
                found.append((name, stmt.lineno))
            for field in ("body", "orelse", "finalbody"):
                inner = getattr(stmt, field, None)
                if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                    visit_block(name, inner)
            for handler in getattr(stmt, "handlers", []) or []:
                visit_block(name, handler.body)

    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit_block(function.name, function.body)
    return found


def test_the_per_assignment_checker_reports_a_cancel_with_no_record():
    source = (
        "def retire(rows):\n"
        "    for row in rows:\n"
        "        if row.a:\n"
        "            row.state = INQUIRY_CANCELLED\n"
        "            _record_cancel(db, row, 'superseded')\n"
        "        else:\n"
        "            row.state = INQUIRY_CANCELLED\n"
        "            row.note = 'x'\n"
    )
    assert unrecorded_cancel_assignments(source) == [("retire", 7)]


def test_the_per_assignment_checker_accepts_a_record_after_each_cancel():
    source = (
        "def retire(rows):\n"
        "    for row in rows:\n"
        "        if row.a:\n"
        "            row.state = 'cancelled'\n"
        "            _record_cancel(db, row, 'superseded')\n"
        "        else:\n"
        "            row.state = INQUIRY_CANCELLED\n"
        "            self._record_cancel(db, row, 'manual')\n"
    )
    assert unrecorded_cancel_assignments(source) == []


def test_every_cancel_assignment_in_the_services_is_followed_by_its_record():
    offenders: list[str] = []
    for path in sorted(SERVICES.rglob("*.py")):
        for name, line in unrecorded_cancel_assignments(path.read_text()):
            offenders.append(f"{path.name}:{line} ({name})")
    assert offenders == [], "cancelled with no _record_cancel( right after: " + ", ".join(offenders)


# --------------------------------------------------------------------------- partly linked branch


def test_retiring_a_placed_row_on_a_dropped_line_records_one_superseded_cancel(api, monkeypatch):
    import uuid
    from types import SimpleNamespace

    from app.models.project_so import INQUIRY_PLACED, OrderInquiry

    fx = _confirmed_order(api, line_no=593)
    world = fx["world"]
    row = fx["row"]
    row.state = INQUIRY_PLACED
    world.db.commit()
    service = ProjectOrderInquiryService(world.db)
    # The row holds only the cascade's own guess, which is what lets the walk retire it.
    monkeypatch.setattr(
        ProjectOrderInquiryService, "_links_by_row",
        lambda self, ids: {str(row.id): [SimpleNamespace(auto=True)]},
    )
    monkeypatch.setattr(ProjectOrderInquiryService, "_unplace_drafts", lambda self, rows, trigger: None)
    monkeypatch.setattr(
        ProjectOrderInquiryService, "_dispatch_changed_with_links", lambda self, *a, **k: None
    )
    inquiry = world.db.query(OrderInquiry).filter(OrderInquiry.id == row.order_inquiry_id).one()
    successor = SimpleNamespace(id=str(uuid.uuid4()), revision_no=2)

    service._retire_uncovered_rows(inquiry, successor, [], actor_user_id=str(world.actor))
    world.db.commit()

    assert row.state == INQUIRY_CANCELLED
    entry = _one_cancel(world, row)
    assert entry.new_values["cause"] == "superseded"
    assert entry.new_values["revision_no"] == 2
    assert entry.new_values["supply_decision_id"] == successor.id
    assert entry.new_values["so_line_no"] == 593
