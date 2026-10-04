#!/usr/bin/env python3
"""One-off: recompute the stored received figures of containers (packing lists).

WHY
---
`inbound_shipment_lines.quantity_received` / `spo_allocated_quantity` /
`line_status` are STORED, and the chatbot's "incoming" answer reads them. They
are recomputed only when the packing list page is opened, or when an ingest or
allocation write touches that shipment. A receipt that reached the container any
other way (the SPO dedupe, a GRN against a sibling row) leaves them stale: on
GCXU6137164 the stored line said 95 received while the page showed 99/99, so the
chatbot reported 4 still incoming. The nightly `spo_container_relink_sweep`
now heals open containers every day; this script does it on demand.

WHAT IT DOES
------------
For each selected shipment it computes the figures exactly as the packing list
page does (`InboundShipmentService.compute_shipment_line_figures`) and prints,
per line, the stored value beside the computed one. DRY-RUN unless --apply;
--apply stores them through `refresh_shipment_line_statuses` (the page's own
code; one commit per shipment).

    --container X   every shipment of the company with that container number
                    (repeatable; case and surrounding spaces ignored)
    --all-open      every shipment of the company with a line not yet `received`

RUN IT IN THE PROD BACKEND CONTAINER
------------------------------------
    cd /opt/sorento-crm2; COLOUR=$(cat .active_color)
    docker compose exec -T -w /app backend_${COLOUR} \\
        python scripts/oneoff/refresh_container_received.py --company SRT --container GCXU6137164 \\
        2>&1 | tee refresh-GCXU6137164-dryrun.log
    # then the same with --apply

Exit codes: 0 done, 1 bad arguments / unknown company / a container with no
shipment (the others still ran), 4 an unexpected database error (that shipment
rolled back, the run STOPPED; shipments before it stay committed).
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text  # noqa: E402

from app.models.base import company_scope  # noqa: E402
from app.models.procurement import InboundShipment  # noqa: E402
from app.services.company_scope import register_company_scope_listeners  # noqa: E402
from app.services.procurement_service import InboundShipmentService  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--company", required=True, help="companies.code (Sorento: SRT)")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--container", action="append", metavar="CONTAINER_NUMBER",
                       help="shipping container number (repeatable)")
    which.add_argument("--all-open", action="store_true",
                       help="every shipment with a line not yet received")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    return parser


def _shipments_for_container(db, company_id: str, container: str) -> list[str]:
    return [
        str(row[0])
        for row in db.execute(
            text(
                "SELECT id FROM inbound_shipments WHERE company_id = :c "
                "AND upper(trim(shipping_container_number)) = upper(trim(:x)) ORDER BY created_at, id"
            ),
            {"c": company_id, "x": container},
        )
    ]


def _report(db, service: InboundShipmentService, shipment_id: str, out) -> int:
    """Print stored vs computed per line; return how many lines would change."""
    shipment = db.query(InboundShipment).filter(InboundShipment.id == shipment_id).one()
    out(f"\n--- {shipment.shipment_number} / container {shipment.shipping_container_number} ({shipment_id}) ---")
    lines = sorted(shipment.shipment_lines, key=lambda l: (l.created_at is None, l.created_at, str(l.id)))
    if not lines:
        out("  no lines")
        return 0
    figures = service.compute_shipment_line_figures(shipment)
    changed = 0
    for line in lines:
        alloc, recv, status = figures[str(line.id)]
        old = (int(line.spo_allocated_quantity or 0), int(line.quantity_received or 0), line.line_status)
        differs = old != (alloc, recv, status)
        changed += differs
        code = line.product.product_code if line.product is not None else line.product_id
        out(f"  line {line.id} {code} shipped {int(line.quantity_shipped or 0)}: "
            f"allocated {old[0]} -> {alloc}, received {old[1]} -> {recv}, status {old[2]} -> {status}"
            f"{'  CHANGED' if differs else ''}")
    return changed


def run(db, company_code: str, *, containers: Optional[list[str]] = None, all_open: bool = False,
        apply: bool = False, out=print) -> int:
    """`db` is an app ORM Session. Returns the exit code."""
    company_id = db.execute(text("SELECT id FROM companies WHERE code = :c"), {"c": company_code}).scalar()
    if company_id is None:
        out(f"no company with code {company_code!r}")
        return 1
    company_id = str(company_id)
    where = db.execute(text("SELECT current_database(), inet_server_addr()")).one()
    out(f"=== database {where[0]} at {where[1] or 'local socket'} ===")
    out(f"=== {company_code} ({company_id}) {'APPLY' if apply else 'DRY-RUN (no writes)'} ===")
    # A plain `python` process never ran the app's startup: without this the
    # scope filter is not installed and `company_scope` narrows nothing.
    register_company_scope_listeners()
    service = InboundShipmentService(db)
    exit_code = 0
    shipment_ids: list[str] = []
    if all_open:
        shipment_ids = service.open_shipment_ids(company_id=company_id)
    for container in containers or []:
        found = _shipments_for_container(db, company_id, container)
        if not found:
            out(f"no shipment for container {container!r} in {company_code}")
            exit_code = 1
        shipment_ids.extend(s for s in found if s not in shipment_ids)
    db.rollback()

    to_change = 0
    for shipment_id in shipment_ids:
        try:
            with company_scope(db, frozenset({company_id})):
                changed = _report(db, service, shipment_id, out)
                to_change += changed
                if apply:
                    service.refresh_shipment_line_statuses(shipment_id)
                    out(f"  stored ({changed} line(s) changed)")
                else:
                    db.rollback()
        except Exception as exc:  # noqa: BLE001 - stop the run, report, never half-write
            db.rollback()
            out(f"  FAILED, rolled back, run STOPPED: {type(exc).__name__}: {exc}")
            return 4
    out(f"\n=== {'APPLIED' if apply else 'DRY-RUN'}: shipments {len(shipment_ids)}, lines to change {to_change} ===")
    return exit_code


def main() -> int:
    args = build_parser().parse_args()
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set (the backend container sets it)")
        return 1
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        return run(db, args.company, containers=args.container, all_open=args.all_open, apply=args.apply)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
