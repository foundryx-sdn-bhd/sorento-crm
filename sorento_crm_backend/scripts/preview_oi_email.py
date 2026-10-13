#!/usr/bin/env python3
"""Preview the order inquiry handover email for one SO line change, without sending it.

EMAIL-SO-CHANGES (crew audit 12 Oct, `documentation/plans/scm/PLAN-email-so-line-changes-12oct.md`).
The owner's test copy has no SMTP, so this renders the email a planning change WOULD send and
writes its HTML to a file.

HOW
---
Runs the real seam inside the board's Simulate confirm dry run (`app/services/confirm_simulation.py`):
an outer savepoint that is ALWAYS rolled back, with `confirm_preview` set so the post-commit drain
never dispatches, plus the guard that refuses any real commit. Inside it the script:

1. moves the SO line in the book (the core line, and its project mirror) as asked:
   `--date`, `--qty` and/or `--item` (a product code that exists);
2. builds the planning-change batch for that one change, confirms its row, and applies it,
   exactly as Fulfilment Planning's Confirm does (`planning_change_service.apply`);
3. collects the handover lines the apply queued (`_record_handover` / `record_stock_short`),
   builds the context with `_build_handover_context`, and renders it through the template
   the enabled "Order inquiry to purchasing" automation names (else
   `order_inquiry_handover_default`) with `EmailTemplateService.render`, the same call the
   automation makes.

Nothing is saved, sent or queued: no email, no outbox row, no notification, no batch.

Run from sorento_crm_backend/:
    venv/bin/python scripts/preview_oi_email.py --so SO400236 --line SRTWB246 --date 2026-11-02 --out /tmp/so400236.html
    venv/bin/python scripts/preview_oi_email.py --so SO314594 --line CB2805A-DIY --qty 150 --out /tmp/qty.html
    venv/bin/python scripts/preview_oi_email.py --so SO421287 --line SRT382-6-DIY --item SRT382-6 --out /tmp/swap.html

`--line` is the line's item code (or its line number). Prints the subject, the headline and the
line table as text, and writes the HTML body to `--out`.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--so", required=True, help="AutoCount SO number, e.g. SO400236")
    parser.add_argument("--line", required=True, help="item code or line number on that SO")
    parser.add_argument("--date", type=date.fromisoformat, help="new delivery date, YYYY-MM-DD")
    parser.add_argument("--qty", type=Decimal, help="new line quantity")
    parser.add_argument("--item", help="new product code (an item swap)")
    parser.add_argument("--actor-email", help="whose name the mail says raised it")
    parser.add_argument("--out", required=True, help="where to write the email HTML")
    args = parser.parse_args(argv)
    if args.date is None and args.qty is None and args.item is None:
        parser.error("say what changes: --date, --qty and/or --item")
    return args


def preview(db, args: argparse.Namespace) -> Dict[str, Any]:
    """Run the change in a rolled-back simulation; return {subject, body_html, body_text,
    lines, headline}. Raises SystemExit with a reason when the SO or line is not found."""
    from app.models.automation import Automation
    from app.models.base import company_scope, set_company_scope
    from app.models.email_template import EmailTemplate
    from app.models.product import Product
    from app.models.project_so import ProjectSalesOrder, ProjectSalesOrderLine
    from app.models.order import SalesOrder, SalesOrderLine
    from app.models.user import User
    from app.services import planning_change_service
    from app.services.confirm_simulation import simulating, simulation_session
    from app.services.email_template_service import EmailTemplateService
    from app.services import project_order_inquiry_service as oi
    from app.services.scm.outstanding_diff import (
        DATE_AND_QTY_CHANGED,
        DATE_MOVED,
        PRODUCT_CHANGED,
        QTY_CHANGED,
        Change,
        Diff,
        Line,
    )

    with company_scope(db, None):
        order = (
            db.query(ProjectSalesOrder)
            .filter(ProjectSalesOrder.autocount_doc_no == args.so)
            .first()
        )
        if order is None:
            raise SystemExit(f"{args.so}: no project sales order with that number")
        lines = (
            db.query(ProjectSalesOrderLine)
            .filter(ProjectSalesOrderLine.project_sales_order_id == order.id)
            .order_by(ProjectSalesOrderLine.line_no)
            .all()
        )
        product_codes = {
            p.id: p.product_code
            for p in db.query(Product).filter(Product.id.in_({l.product_id for l in lines}))
        }
        wanted = [
            l for l in lines
            if str(l.line_no) == args.line or product_codes.get(l.product_id) == args.line
        ]
        if len(wanted) != 1:
            raise SystemExit(
                f"{args.so}: --line {args.line} names {len(wanted)} lines; "
                f"use the line number ({', '.join(str(l.line_no) for l in lines)})"
            )
        line = wanted[0]
        core = db.get(SalesOrderLine, line.core_sales_order_line_id)
        core_so = db.get(SalesOrder, order.so_id)
        if core is None or core_so is None:
            raise SystemExit(f"{args.so}: the line has no AutoCount book line to move")
        new_product = None
        if args.item:
            new_product = db.query(Product).filter(Product.product_code == args.item).first()
            if new_product is None:
                raise SystemExit(f"--item {args.item}: no product with that code")
        actor = (
            db.query(User).filter(User.email == args.actor_email).first()
            if args.actor_email
            else None
        )
        actor_id = str(actor.id) if actor else None
        automation = (
            db.query(Automation)
            .filter(Automation.trigger_type == "order_inquiry_handover", Automation.enabled.is_(True))
            .first()
        )
        template_id = automation.email_template_id if automation else None
        company_id = order.company_id

    captured: List[Dict[str, Any]] = []
    record = oi.ProjectOrderInquiryService._record_handover
    record_short = oi.ProjectOrderInquiryService.record_stock_short

    def _keep_last(service) -> None:
        pending = service.db.info.get(oi._HANDOVER_PENDING_KEY) or []
        if pending and (not captured or captured[-1] is not pending[-1]):
            captured.append(pending[-1])

    def _record(self, *a, **kw):
        record(self, *a, **kw)
        _keep_last(self)

    def _record_short(self, *a, **kw):
        record_short(self, *a, **kw)
        _keep_last(self)

    old_code = product_codes.get(line.product_id) or ""
    old_qty = Decimal(str(core.qty_ordered))
    old_date = core.required_date
    new_qty = args.qty if args.qty is not None else old_qty
    new_date = args.date or old_date
    if new_product is not None:
        kind = PRODUCT_CHANGED
    elif new_qty != old_qty and new_date != old_date:
        kind = DATE_AND_QTY_CHANGED
    elif new_qty != old_qty:
        kind = QTY_CHANGED
    else:
        kind = DATE_MOVED

    oi.ProjectOrderInquiryService._record_handover = _record
    oi.ProjectOrderInquiryService.record_stock_short = _record_short
    try:
        with simulating(), simulation_session(db) as sim:
            set_company_scope(sim, frozenset({company_id}))
            sim_core = sim.get(SalesOrderLine, core.id)
            sim_line = sim.get(ProjectSalesOrderLine, line.id)
            sim_core.qty_ordered = new_qty
            sim_core.required_date = new_date
            sim_line.qty = new_qty
            sim_line.delivery_date = new_date
            if new_product is not None:
                sim_core.product_id = new_product.id
                sim_line.product_id = new_product.id
            sim.commit()
            new_code = new_product.product_code if new_product is not None else old_code
            change = Change(
                kind, core_so.so_number, new_code, "",
                before=Line(doc_number=core_so.so_number, item_code=old_code, location="",
                            qty=float(old_qty), required_date=old_date, row_ref=str(core.id),
                            line_id=str(core.id)),
                after=Line(doc_number=core_so.so_number, item_code=new_code, location="",
                           qty=float(new_qty), required_date=new_date, row_ref=str(core.id),
                           line_id=str(core.id)),
            )
            batch = planning_change_service.build_batch(
                sim, Diff(scope_documents=(core_so.so_number,), changes=[change]),
                applied_line_ids={id(change): str(core.id)},
                order_ids={core_so.so_number: str(core_so.id)}, actor=actor_id,
                import_job_id=None, file_name="preview_oi_email.py",
            )
            sim.commit()
            if batch is None:
                raise SystemExit(
                    f"{args.so} {args.line}: the change raised no planning-change row "
                    "(the line holds no decision or order inquiry row)"
                )
            out = planning_change_service.get_batch(sim, str(batch.id))
            for order_out in out["orders"]:
                for row_out in order_out["rows"]:
                    planning_change_service.set_row_decision(
                        sim, str(batch.id), row_out["id"], "confirm"
                    )
            result = planning_change_service.apply(sim, str(batch.id), actor_id, notify=False)
            if result.get("failed_orders"):
                raise SystemExit(f"apply refused: {result['failed_orders']}")

            from app.services import so_line_attachments

            core_line_ids = [i["core_line_id"] for i in captured if i.get("core_line_id")]
            attachments = (
                so_line_attachments.handover_attachments(sim, core_line_ids)
                if core_line_ids
                else {}
            )
            built = oi._build_handover_context(captured, attachments)
            if built is None:
                raise SystemExit("the change sends no email (nothing for purchasing to read)")
            context, _source_id = built
            template = (
                sim.get(EmailTemplate, template_id)
                if template_id
                else sim.query(EmailTemplate)
                .filter(EmailTemplate.code == "order_inquiry_handover" + "_default")
                .first()
            )
            rendered = EmailTemplateService(sim).render(template, dict(context))
            return {
                "subject": rendered["subject"],
                "body_html": rendered["body_html"],
                "body_text": rendered.get("body_text") or "",
                "headline": context["handover"]["headline"],
                "lines": context["handover"]["lines"],
            }
    finally:
        oi.ProjectOrderInquiryService._record_handover = record
        oi.ProjectOrderInquiryService.record_stock_short = record_short


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        result = preview(db, args)
    finally:
        db.rollback()
        db.close()
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(result["body_html"])
    print(f"Subject: {result['subject']}")
    print(f"Headline: {result['headline']}")
    print(result["body_text"])
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
