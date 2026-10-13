#!/usr/bin/env python3
"""Re-decide every stored AutoCount cancel line of one company under the exact rule (AC-CANCEL-CRM).

Why: a database where the old matcher ran (the lane's crew copy) still holds its guesses, the
`tie_break` / `fallback` rows and the `qty_cancelled` they put on a line, and re-pushing the same
cancel document is `unchanged`, so nothing would ever re-decide them. Production never ran the
old matcher (its tables arrive with this lane), so on production this finds nothing to move.

What: every Cancel SO / Cancel PO line of the company is matched again from what is stored, by
the same code the ingest runs (`rematch_lines`, which decides each line's whole group, then
`recompute_cancelled`), in batches of `MAX_WAITING_LINES`. Consignment documents are stored only
and are skipped. With `--apply`, each batch commits and then runs the ingest's own post-commit
hooks (a line that became fully cancelled retires its OI rows; a partial cancel that moved a
live line's planned qty raises the planning change). OI rows already retired stay retired.
A hook that fails is logged (`ingest.cancel_oi_hook_failed` / `ingest.cancel_planning_change_
failed`) and, as on the ingest, is not redone by a re-run: the line decision it followed is
already committed, so a second run sees no change. Check the log after `--apply`.

Dry run by default: counts only, never a row, and it ends with a rollback. Re-running after
`--apply` changes nothing.

Run from sorento_crm_backend/ with the deployment's env:
    venv/bin/python scripts/autocount_cancel_redecide.py --company SRT            # dry run
    venv/bin/python scripts/autocount_cancel_redecide.py --company SRT --apply
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.models.autocount_cancel import (  # noqa: E402
    KIND_CANCEL_PO,
    KIND_CANCEL_SO,
    AutocountCancelDoc,
    AutocountCancelLine,
)
from app.models.base import company_scope  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.services.autocount_cancel_ingest_service import (  # noqa: E402
    MAX_WAITING_LINES,
    Derived,
    Touched,
    recompute_cancelled,
    rematch_lines,
    run_cancel_hooks,
)
from app.services.company_scope import register_company_scope_listeners  # noqa: E402


def _state(db, company_id: str) -> dict[str, tuple]:
    rows = (
        db.query(AutocountCancelLine.id, AutocountCancelLine.match_status,
                 AutocountCancelLine.match_reason, AutocountCancelLine.sales_order_line_id,
                 AutocountCancelLine.purchase_order_line_id, AutocountCancelLine.spo_allocation_id,
                 AutocountCancelDoc.kind)
        .join(AutocountCancelDoc, AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
        .filter(AutocountCancelLine.company_id == company_id,
                AutocountCancelDoc.kind.in_((KIND_CANCEL_SO, KIND_CANCEL_PO)))
        .order_by(AutocountCancelLine.created_at, AutocountCancelLine.id)
        .all()
    )
    return {str(r[0]): (r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows}


def run(db, company_id: str, dry_run: bool = True, batch: int = MAX_WAITING_LINES) -> dict:
    """{lines, changed, moves: {"status:reason -> status:reason": n}, newly_cancelled,
    plan_moves}. `dry_run` defaults to True so a caller that forgets it previews."""
    register_company_scope_listeners()
    company_id = str(company_id)
    summary = {"lines": 0, "changed": 0, "moves": {}, "newly_cancelled": 0, "plan_moves": 0}
    with company_scope(db, frozenset({company_id})):
        before = _state(db, company_id)
        ids = list(before)
        summary["lines"] = len(ids)
        for start in range(0, len(ids), batch):
            chunk = ids[start:start + batch]
            lines = db.query(AutocountCancelLine).filter(AutocountCancelLine.id.in_(chunk)).all()
            kinds = {str(l.cancel_doc_id): before[str(l.id)][5] for l in lines}
            touched = Touched()
            rematch_lines(db, company_id, lines, kinds, touched)
            derived: Derived = recompute_cancelled(db, touched)
            summary["newly_cancelled"] += len(derived.newly_cancelled)
            summary["plan_moves"] += len(derived.plan_moves)
            if dry_run:
                db.flush()
            else:
                db.commit()
                run_cancel_hooks(db, SimpleNamespace(derived=derived), actor=None)
        after = _state(db, company_id)
    for line_id, was in before.items():
        now = after.get(line_id)
        if now is None or now[:5] == was[:5]:
            continue
        summary["changed"] += 1
        move = f"{was[0]}:{was[1]} -> {now[0]}:{now[1]}"
        summary["moves"][move] = summary["moves"].get(move, 0) + 1
    if dry_run:
        db.rollback()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--company", required=True, help="Company CODE")
    parser.add_argument("--apply", action="store_true", help="Write (the default is a dry run)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.code == args.company).first()
        if company is None:
            print(f"no company with code {args.company!r}")
            return 1
        summary = run(db, str(company.id), dry_run=not args.apply)
        print(f"mode: {'APPLIED' if args.apply else 'DRY-RUN (no writes)'}")
        print(f"cancel lines: {summary['lines']}  changed: {summary['changed']}")
        for move, count in sorted(summary["moves"].items()):
            print(f"  {move}: {count}")
        print(f"lines newly fully cancelled: {summary['newly_cancelled']}  "
              f"planned qty moves: {summary['plan_moves']}")
        if not args.apply and summary["changed"]:
            print("Re-run with --apply to write these changes.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
