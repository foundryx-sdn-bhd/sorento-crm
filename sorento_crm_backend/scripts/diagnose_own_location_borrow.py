"""Why does a board line's own location not appear under Borrow? (BORROW-OWN-LOC, read-only)

Usage (from sorento_crm_backend/, against the dev DB, never production):

    venv/bin/python scripts/diagnose_own_location_borrow.py SO424764 144

Builds the Fulfilment Planning board for the order exactly as the screen does, then prints:
  1. the line's own location, its reserve-window state and the Borrow list it was offered;
  2. every OTHER open sales-order line of the same product at the own location, with the
     reason each one is or is not offered as a Borrow donor;
  3. every confirmed hold at the own location and whose order holds it.

Everything runs inside one transaction that is rolled back; nothing is written.
"""
from __future__ import annotations

import sys
from decimal import Decimal


def main(so_number: str, line_no: int) -> None:
    from app.database import SessionLocal
    from app.models.base import company_scope
    from app.services.company_scope import register_company_scope_listeners

    register_company_scope_listeners()
    db = SessionLocal()
    try:
        with company_scope(db, None):  # read-only diagnostic: every company
            _diagnose(db, so_number, line_no)
    finally:
        db.rollback()
        db.close()


def _diagnose(db, so_number: str, line_no: int) -> None:
    from app.models.order import SalesOrder, SalesOrderLine
    from app.services.project_fulfilment_board_service import FulfilmentBoardService

    board_service = FulfilmentBoardService(db)
    supply = board_service.supply
    captured: dict = {}
    real_demand_facts = supply.demand_facts

    def capture(*args, **kwargs):
        facts = real_demand_facts(*args, **kwargs)
        captured.update(facts)
        return facts

    supply.demand_facts = capture  # type: ignore[method-assign]
    board = board_service.build([so_number], granularity="week")

    contribution = None
    for cell in board["cells"]:
        for c in cell.get("contributions") or []:
            if c.get("so_number") == so_number and str(c.get("line_no")) == str(line_no):
                contribution = c
    if contribution is None:
        print(f"{so_number} line {line_no} is not on the board (closed, unplannable?)")
        return
    print(f"== {so_number} line {line_no} {contribution.get('item_code')}")
    print(f"own location      : {contribution.get('fulfilment_location')}")
    print(f"outside window    : {contribution.get('outside_reserve_window')}")
    print(f"borrow switch ON  : {supply.borrow_from_order}")
    print("borrow offered    :")
    for cand in contribution.get("borrow_candidates") or []:
        print(
            "   ", cand.get("warehouse_code"), cand.get("rung"), cand.get("source"),
            cand.get("donor_so_number"), cand.get("donor_line_no"),
            "qty", cand.get("free_qty"),
        )

    fact = captured.get(contribution.get("key"))
    if fact is None or fact.warehouse is None:
        print("no fact / no own location resolved for this line")
        return
    own_id = str(fact.warehouse.id)
    print(f"group code        : {fact.group_code}")
    if fact.core is None:
        print("asker core line   : NONE (no core sales-order line -> no group pile, no donors)")
    else:
        core_order = db.query(SalesOrder).filter(SalesOrder.id == fact.core.sales_order_id).first()
        print(
            "asker core line   :",
            "line_status", fact.core.line_status,
            "| purchasing_status", fact.core.purchasing_status,
            "| qty_ordered", fact.core.qty_ordered,
            "| qty_required", fact.core.qty_required,
            "| qty_delivered", fact.core.qty_delivered,
            "| order status", getattr(core_order, "status", None),
            "| is_transferable", getattr(core_order, "is_transferable", None),
        )
    members = supply._group_pile_members(fact)
    member_ids = {m["line_id"] for m in members}
    asker_in_pile = fact.core is not None and str(fact.core.id) in member_ids
    print(f"asker in pile     : {asker_in_pile} (pile has {len(members)} open lines)")
    if not asker_in_pile:
        print("   -> _group_borrow_donors returns NO donors for this line at all (:4436)")
    assignment = supply.planning_assignments([fact.product_id]).get(str(fact.product_id))
    status_of = {}
    if assignment is not None:
        for result in assignment.lines:
            on_hand_at = {}
            for item in result.assigned:
                if item.event.kind == "on_hand" and item.event.warehouse:
                    on_hand_at[item.event.warehouse] = on_hand_at.get(
                        item.event.warehouse, 0
                    ) + float(item.qty)
            status_of[str(result.line.key)] = (result.status, on_hand_at)
    decided = supply._decided_elsewhere_cached()
    donors = {d["donor_core_line_id"]: d for d in supply._group_borrow_donors(fact)}
    step_two = {
        d["donor_core_line_id"]: d for d in supply.order_borrow_candidates_for(fact)
    }
    mine_so = fact.core.sales_order_id if fact.core is not None else None
    print(f"\n== other open lines of this product at {fact.own_code}")
    rows = (
        db.query(SalesOrderLine, SalesOrder)
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
        .filter(
            SalesOrderLine.product_id == fact.product_id,
            SalesOrderLine.warehouse_id == own_id,
            SalesOrder.status == "open",
        )
        .order_by(SalesOrderLine.required_date)
        .all()
    )
    for line, order in rows:
        open_qty = Decimal(line.qty_ordered or 0) - Decimal(line.qty_delivered or 0)
        if open_qty <= 0:
            continue
        key = str(line.id)
        if mine_so is not None and order.id == mine_so:
            why = "same sales order (never a donor)"
        elif key in step_two:
            why = "OFFERED as step 2 (later order that can wait)"
        elif key in decided:
            why = "covered by an active decision -> not a group-borrow donor"
        elif key in donors:
            d = donors[key]
            if d["same_agent"] or d["lower_ranked"]:
                if not supply.borrow_from_order and not d["lower_ranked"]:
                    why = "same agent but ranked ahead, switch OFF -> dropped"
                else:
                    why = "OFFERED as group borrow"
            else:
                why = "other agent ranked AHEAD of this line -> not offered"
        else:
            why = (
            "not open/transferable demand in the group pile"
            if key not in member_ids
            else "in the pile, but no donor list (asker not in pile)"
        )
        status, on_hand_at = status_of.get(key, (None, {}))
        print(
            "   ", order.so_number, "req", line.required_date, "open", open_qty,
            "| transferable", order.is_transferable, "| line", line.line_status,
            "| purch", line.purchasing_status, "| in pile", key in member_ids,
            "| assignment", status, "on hand", on_hand_at or "-",
            "->", why,
        )

    print(f"\n== confirmed holds at {fact.own_code}")
    for hold in supply.holds_at_locations([fact.product_id], [own_id]):
        print("   ", hold["so_number"], "req", hold["required_date"], "qty", hold["qty"])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1], int(sys.argv[2]))
