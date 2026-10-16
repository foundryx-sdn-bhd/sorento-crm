#!/usr/bin/env python3
"""One-off: cancel the raised order rows a closed or cancelled sales-order line left behind.

WHY
---
Before OI-CLOSED-LINE a Confirm on a line whose AutoCount line was `closed` was superseded
("the sales-order line is closed") and never applied, so the order row it would have retired
stayed raised on the order inquiry. A decision now applies on a closed line, but the rows
already stranded need a one-off pass.

WHAT IT DOES
------------
For ONE order inquiry (`--inquiry OI-0123`) it cancels every RAISED ORDER / ORDER_BACK row
whose project line has a planning-change row superseded "the sales-order line is closed", or
whose core line is `cancelled`, and notes it as dropped with the composition that was
dropped. `--cancel-balance ITEM` (repeatable) also cancels the RAISED CANCEL_BALANCE rows of
that item. Placed, partly linked and actioned rows are never touched. Each cancelled row is
queued through `_record_handover`, so the one existing handover email goes out after the
commit. A second run finds nothing to do.

DRY-RUN unless --apply: it reports the rows it would cancel and writes nothing.

    venv/bin/python scripts/oneoff/oi_closed_line_cleanup.py --inquiry OI-0123
    venv/bin/python scripts/oneoff/oi_closed_line_cleanup.py --inquiry OI-0123 --apply \\
        --cancel-balance ITEM-A --cancel-balance ITEM-B
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.models.base import company_scope, set_company_scope  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow  # noqa: E402
from app.models.project_so import (  # noqa: E402
    INQUIRY_CANCELLED,
    INQUIRY_RAISED,
    IV_CANCEL_BALANCE,
    IV_ORDER,
    IV_ORDER_BACK,
    OrderInquiry,
    OrderInquiryRow,
    ProjectSalesOrderLine,
    SOSupplyDecision,
)
from app.models.order import SalesOrderLine  # noqa: E402
from app.services.project_order_inquiry_service import ProjectOrderInquiryService  # noqa: E402

CLOSED_REASON = "the sales-order line is closed"
_ZERO = Decimal("0")
# Written onto a snapshot without CS re-deciding the line: a planning change stamps
# `late_days` on the active revision in place, and a carry moves a swapped product's id and
# code onto its copy.
_PATCHED_WITHOUT_A_DECISION = frozenset({"late_days", "product_id", "item_code"})


def _composition_text(composition: Any) -> str:
    if not composition:
        return "its confirmed composition"
    if isinstance(composition, dict):
        parts = [f"{k} {v}" for k, v in composition.items() if v not in (None, "", [], 0, "0")]
        return ", ".join(parts) or "its confirmed composition"
    return str(composition)


def _report(row, verb_note, core, reason) -> Dict[str, Any]:
    return {
        "row_id": str(row.id),
        "item_code": row.item_code,
        "verb": row.verb,
        "qty": row.qty,
        "ordered": core.qty_ordered if core is not None else None,
        "delivered": core.qty_delivered if core is not None else None,
        "reason": reason,
        "action": "cancel",
    }


def _fresh_decision_times(db, project_sales_order_id, project_line_id) -> Optional[List[Any]]:
    """When CS actually decided the line: the `confirmed_at` of every revision whose snapshot
    of it differs from the previous revision's (or the previous one did not cover it).

    A revision that only CARRIES a line copies its old snapshot verbatim (`_write_decision`)
    and cancels-and-re-raises its raised row under the new revision, so neither "some later
    revision names the line" nor the live row's `created_at` says CS looked at it again.
    Keys a writer patches without a decision (`_PATCHED_WITHOUT_A_DECISION`) are ignored.
    A re-confirm identical in every other key (`open_qty` included) reads as carried; the dry
    run shows such a row before anything is applied. None when no revision covers the line.
    """
    revisions = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == project_sales_order_id)
        .order_by(SOSupplyDecision.revision_no)
        .all()
    )
    times: List[Any] = []
    covered = False
    previous: Optional[Dict[str, Any]] = None
    for decision in revisions:
        snap = next(
            (
                {k: v for k, v in s.items() if k not in _PATCHED_WITHOUT_A_DECISION}
                for s in (decision.line_snapshots or [])
                if str((s or {}).get("project_line_id")) == str(project_line_id)
            ),
            None,
        )
        if snap is not None:
            covered = True
            if snap != previous:
                times.append(decision.confirmed_at)
        previous = snap
    return times if covered else None


def run(
    db,
    inquiry_no: str,
    *,
    apply: bool = False,
    cancel_balance_items: Sequence[str] = (),
    company_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Cancel (or, without `apply`, only report) the stranded rows of one inquiry."""
    query = db.query(OrderInquiry).filter(OrderInquiry.inquiry_no == inquiry_no)
    if company_code:
        query = query.join(Company, Company.id == OrderInquiry.company_id).filter(
            Company.code == company_code
        )
    matches = query.all()
    if not matches:
        raise ValueError(f"No order inquiry {inquiry_no}")
    if len(matches) > 1:
        raise ValueError(
            f"{inquiry_no} exists in {len(matches)} companies; name one with --company"
        )
    inquiry = matches[0]
    service = ProjectOrderInquiryService(db)
    balance_items = {str(item).strip() for item in cancel_balance_items if str(item).strip()}

    raised = (
        db.query(OrderInquiryRow)
        .filter(
            OrderInquiryRow.order_inquiry_id == inquiry.id,
            OrderInquiryRow.state == INQUIRY_RAISED,
        )
        .order_by(OrderInquiryRow.created_at, OrderInquiryRow.id)
        .all()
    )
    report: List[Dict[str, Any]] = []
    for row in raised:
        core: Optional[SalesOrderLine] = None
        dropped: Optional[PlanningChangeRow] = None
        if row.so_line_id:
            core = (
                db.query(SalesOrderLine)
                .join(
                    ProjectSalesOrderLine,
                    ProjectSalesOrderLine.core_sales_order_line_id == SalesOrderLine.id,
                )
                .filter(ProjectSalesOrderLine.id == row.so_line_id)
                .first()
            )
            # Only a line nobody re-decided after the dropped decision was applied belongs
            # to this clean-up. Read off the line's decision history; a line no revision
            # covers falls back to the row's own raise time.
            candidates = (
                db.query(PlanningChangeRow, PlanningChangeBatch.applied_at)
                .join(PlanningChangeBatch, PlanningChangeBatch.id == PlanningChangeRow.batch_id)
                .filter(
                    PlanningChangeRow.project_line_id == row.so_line_id,
                    PlanningChangeRow.applied_reason == CLOSED_REASON,
                )
                .order_by(PlanningChangeRow.created_at.desc())
                .all()
            )
            fresh = (
                _fresh_decision_times(db, candidates[0][0].project_sales_order_id, row.so_line_id)
                if candidates
                else None
            )
            for candidate, applied_at in candidates:
                cutoff = applied_at or candidate.created_at
                if cutoff is None:
                    in_scope = True
                elif fresh is not None:
                    in_scope = not any(t is not None and t > cutoff for t in fresh)
                else:
                    in_scope = row.created_at is None or row.created_at <= cutoff
                if in_scope:
                    dropped = candidate
                    break
        reason: Optional[str] = None
        if row.verb in (IV_ORDER, IV_ORDER_BACK):
            if dropped is not None:
                reason = CLOSED_REASON
            elif (
                core is not None
                and (core.line_status or "open") == "cancelled"
            ):
                reason = "the sales-order line is cancelled"
        elif row.verb == IV_CANCEL_BALANCE and row.item_code in balance_items:
            reason = "the balance was cancelled by the planner"
        if reason is None:
            continue
        report.append(_report(row, None, core, reason))
        if not apply:
            continue
        was_qty = row.qty
        composition = _composition_text(dropped.composition_json if dropped is not None else None)
        row.state = INQUIRY_CANCELLED
        stamp = f"Dropped: {reason}; composition dropped: {composition}."
        row.note = f"{row.note}; {stamp}" if row.note else stamp
        service._record_handover(
            row, kind="cancelled", was={"qty": was_qty}, actor_user_id=inquiry.raised_by
        )
    if apply:
        db.flush()
    return {"rows": report, "applied": bool(apply)}


def main(argv: Optional[Sequence[str]] = None, db=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--inquiry", required=True, help="inquiry number, e.g. OI-0123")
    parser.add_argument("--company", default=None, help="companies.code, when the number is shared")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    parser.add_argument(
        "--cancel-balance", action="append", default=[], metavar="ITEM_CODE",
        help="also cancel the raised CANCEL_BALANCE rows of this item (repeatable)",
    )
    args = parser.parse_args(argv)

    owns_db = db is None
    if db is None:
        from app.database import SessionLocal

        db = SessionLocal()
    try:
        set_company_scope(db, None)
        with company_scope(db, None):
            out = run(
                db, args.inquiry, apply=args.apply,
                cancel_balance_items=args.cancel_balance, company_code=args.company,
            )
            for r in out["rows"]:
                print(
                    f"cancel {r['row_id']} {r['item_code']} {r['verb']} qty {r['qty']} "
                    f"ordered {r['ordered']} delivered {r['delivered']}: {r['reason']}"
                )
            if args.apply:
                db.commit()
            else:
                db.rollback()
        print(f"{'APPLIED' if args.apply else 'DRY-RUN'}: {len(out['rows'])} row(s)")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_db:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
