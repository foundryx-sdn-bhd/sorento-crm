#!/usr/bin/env python3
"""Re-project every stored AutoCount Consignment (CG) of one company (CONSIGN-DELIVERED-FIX).

Why: ss#125 (14 Oct) projected the CGs as consignment sales orders with delivered =
TransferedQty, so they read as open demand. TransferedQty on a CG line is the RETURNED qty; the
goods left with the CG (owner ruling, 14 Oct). The fixed projection writes delivered = Qty and a
closed order, but a deploy does not rewrite the orders already stored, and SS re-pushes a CG
only when it changes.

What: every stored consignment document of the company (`autocount_cancel_docs`, kind
`consignment`) that the ingest projects (it states TransferedQty, or already has its order) is
written again from its STORED `source_record` by the ingest's own projection
(`AutocountCancelIngestService._project_consignment`, which also folds TransferedQty into
`qty_returned`). In batches of `BATCH`; with `--apply` each batch commits and then runs the
consignments route's own post-commit hooks (plan exception batch, planning change for CS).

Dry run by default: counts only, ends with a rollback. Idempotent: a second `--apply` changes
nothing.

Prod, right after the deploy, in the live backend container (plain python, no venv):
    docker compose exec backend_<color> python scripts/reproject_consignments.py --company SRT
    docker compose exec backend_<color> python scripts/reproject_consignments.py --company SRT --apply
A dev checkout runs it from sorento_crm_backend/ as `venv/bin/python scripts/...`.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.models.autocount_cancel import KIND_CONSIGNMENT, AutocountCancelDoc  # noqa: E402
from app.models.base import company_scope  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.order import DOC_TYPE_CONSIGNMENT, SalesOrder, SalesOrderLine  # noqa: E402
from app.services.autocount_cancel_ingest_service import (  # noqa: E402
    AutocountCancelIngestService,
)
from app.services.autocount_consignment import carries_transfers  # noqa: E402
from app.services.company_scope import register_company_scope_listeners  # noqa: E402
from app.services.master_ingest_service import IngestOutcome  # noqa: E402

logger = logging.getLogger(__name__)

BATCH = 200


def _state(db, company_id: str, doc_no: str):
    """The order's demand-relevant state, to count what moved."""
    order = (db.query(SalesOrder.id, SalesOrder.status)
             .filter(SalesOrder.company_id == company_id, SalesOrder.so_number == doc_no,
                     SalesOrder.doc_type == DOC_TYPE_CONSIGNMENT).one_or_none())
    if order is None:
        return None
    lines = (db.query(SalesOrderLine.source_ref, SalesOrderLine.qty_ordered,
                      SalesOrderLine.qty_delivered, SalesOrderLine.qty_returned,
                      SalesOrderLine.line_status)
             .filter(SalesOrderLine.sales_order_id == order.id)
             .order_by(SalesOrderLine.source_ref).all())
    return order.status, tuple(tuple(l) for l in lines)


def run(db, company_id: str, dry_run: bool = True, commit: bool = True,
        batch: int = BATCH) -> dict:
    """{docs, changed, failed, statuses: {"before -> after": n}}. `dry_run` defaults to True
    so a caller that forgets it previews. `commit=False` (tests) flushes instead of
    committing and runs no hooks."""
    from app.api.v1.external.ingest import _run_consignment_order_hooks

    register_company_scope_listeners()
    company_id = str(company_id)
    summary = {"docs": 0, "changed": 0, "failed": 0, "statuses": {}}
    with company_scope(db, frozenset({company_id})):
        docs = (db.query(AutocountCancelDoc.id, AutocountCancelDoc.source_book,
                         AutocountCancelDoc.doc_no)
                .filter(AutocountCancelDoc.company_id == company_id,
                        AutocountCancelDoc.kind == KIND_CONSIGNMENT)
                .order_by(AutocountCancelDoc.doc_date, AutocountCancelDoc.doc_key).all())
        for start in range(0, len(docs), batch):
            services: dict[str, AutocountCancelIngestService] = {}
            for doc_id, book, doc_no in docs[start:start + batch]:
                service = services.get(book)
                if service is None:
                    service = services[book] = AutocountCancelIngestService(
                        db, None, company_id=company_id, book=book)
                doc = db.get(AutocountCancelDoc, doc_id)
                if not (carries_transfers(doc) or service._has_order(doc_no)):
                    continue
                summary["docs"] += 1
                before = _state(db, company_id, doc_no)
                savepoint = db.begin_nested()
                try:
                    record = service._project_consignment(str(doc_id))
                    if record.outcome in (IngestOutcome.FAILED, IngestOutcome.RETRYABLE):
                        savepoint.rollback()
                        summary["failed"] += 1
                        logger.warning("reproject.failed doc=%s errors=%s", doc_no,
                                       sorted((record.errors or {}).keys()))
                        continue
                    savepoint.commit()
                except Exception:  # noqa: BLE001 - one document, not the run
                    savepoint.rollback()
                    summary["failed"] += 1
                    logger.warning("reproject.failed doc=%s", doc_no, exc_info=True)
                    continue
                after = _state(db, company_id, doc_no)
                if after != before:
                    summary["changed"] += 1
                    move = f"{before[0] if before else 'none'} -> {after[0] if after else 'none'}"
                    summary["statuses"][move] = summary["statuses"].get(move, 0) + 1
            if dry_run or not commit:
                db.flush()
            else:
                db.commit()
                for service in services.values():
                    _run_consignment_order_hooks(db, service, actor=None)
    if dry_run:
        db.rollback()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--company", required=True, help="Company CODE")
    parser.add_argument("--apply", action="store_true", help="Write (the default is a dry run)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from app.services.audit_service import register_audit_listeners

    register_audit_listeners()
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.code == args.company).first()
        if company is None:
            print(f"no company with code {args.company!r}")
            return 1
        summary = run(db, str(company.id), dry_run=not args.apply)
        print(f"mode: {'APPLIED' if args.apply else 'DRY-RUN (no writes)'}")
        print(f"consignments projected: {summary['docs']}  changed: {summary['changed']}  "
              f"failed: {summary['failed']}")
        for move, count in sorted(summary["statuses"].items()):
            print(f"  status {move}: {count}")
        if not args.apply and summary["changed"]:
            print("Re-run with --apply to write these changes.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
