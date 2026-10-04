#!/usr/bin/env python3
"""Merge customers that share a debtor code, one customer per code per company (DEBTOR-CODE-UNIQUE).

Card: documentation/plans/autocount/CARD-debtor-code-unique-04oct.md. Logic:
`app/services/customer_merge.py`. Never run by alembic or on deploy: dev copy first, then
shared dev (crew), then prod only with the owner's approval.

Run from sorento_crm_backend/:

    venv/bin/python scripts/merge_customers_by_code.py                  # dry run, writes the plan CSV
    venv/bin/python scripts/merge_customers_by_code.py --code 301-S007  # one code
    venv/bin/python scripts/merge_customers_by_code.py --apply --actor ops@example.com --resolutions scripts/out/<plan>.csv
    venv/bin/python scripts/merge_customers_by_code.py --apply --actor ops@example.com --create-index
    venv/bin/python scripts/merge_customers_by_code.py --revert dcu-20261004T120000 --actor ops@example.com

- The dry run changes nothing. Its CSV (one row per group: survivor, retired rows, rows that
  would move per table, conflicts) holds real customer data: it lands in `scripts/out/`
  (gitignored) and is never committed.
- A group with a conflict (customer group, phone, active, agent, segment, owner differ) is
  skipped by `--apply` until its `keep` column names the row whose values win.
- A group whose retired names are another business (`held=name_mismatch`, never a `301-*` cash
  code) is held by `--apply` until its `release` column says `yes`. The unique index waits for
  every held and conflict group: it is created only when no code holds two rows.
- `--apply` commits once per company and prints the run id; `--revert <run id>` undoes it.
- `--create-index` adds the code-unique index after a clean apply, and refuses while any code
  still holds two rows.
Take a `pg_dump` of the database first, and run `--apply` in a quiet window: it holds row locks
on every customer it merges for the whole of a company's transaction, so a live write that
references one of them waits for the run.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.base import set_company_scope  # noqa: E402
from app.services import customer_merge  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent / "out"


def _company_id(db, value: str | None) -> str | None:
    if not value:
        return None
    row = db.execute(
        text("SELECT id::text FROM companies WHERE id::text = :v OR lower(code) = lower(:v)"),
        {"v": value},
    ).first()
    if row is None:
        raise SystemExit(f"unknown company {value!r}")
    return row[0]


def _actor_id(db, value: str | None) -> str:
    """The operator named on every audit row a write run leaves (security review S3)."""
    if not value:
        raise SystemExit("--actor <email or user id> is required for --apply and --revert")
    row = db.execute(
        text("SELECT id::text FROM users WHERE id::text = :v OR lower(email) = lower(:v)"),
        {"v": value},
    ).first()
    if row is None:
        raise SystemExit(f"unknown user {value!r}")
    return row[0]


def _summary(groups: list[customer_merge.Group]) -> str:
    rows = sum(len(g.retired_ids) + 1 for g in groups)
    conflicts = [g for g in groups if g.conflicts]
    fields = Counter(f for g in conflicts for f in {c["field"] for c in g.conflicts})
    moved = Counter()
    for g in groups:
        moved.update(g.fk_counts)
    lines = [
        f"groups: {len(groups)}  rows: {rows}  retired: {rows - len(groups)}",
        f"conflict groups: {len(conflicts)}  by field: {dict(fields)}",
        f"kept because: {dict(Counter(g.kept_reason for g in groups))}",
        f"held (name mismatch, not merged without release=yes): {sum(1 for g in groups if g.held)}  "
        f"groups with a name mismatch: {sum(1 for g in groups if g.name_mismatch)}  "
        f"with several AutoCount refs: {sum(1 for g in groups if g.ref_conflicts)}",
        f"rows that move: {dict(moved)}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: plan only, change nothing")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--revert", metavar="RUN_ID")
    parser.add_argument(
        "--actor",
        help="the user running it (email or id); required for --apply and --revert, named on every audit row",
    )
    parser.add_argument("--company", help="company id or code (default: every company)")
    parser.add_argument("--code", action="append", help="limit to this debtor code (repeatable)")
    parser.add_argument("--resolutions", help="a plan CSV with the owner's `keep` column filled")
    parser.add_argument("--create-index", action="store_true", help="after --apply: add the unique index")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    db = SessionLocal()
    set_company_scope(db, None)  # a maintenance run sees every company; filters are explicit
    try:
        actor = _actor_id(db, args.actor) if (args.apply or args.revert) else None
        if args.revert:
            n = customer_merge.revert(db, args.revert, actor_user_id=actor)
            db.commit()
            print(f"reverted run {args.revert}: {n} customers restored")
            return 0

        company_id = _company_id(db, args.company)
        groups = customer_merge.plan(db, company_id=company_id, codes=args.code)
        plan_path = out_dir / f"customer_merge_plan_{stamp}.csv"
        customer_merge.write_plan_csv(groups, plan_path)
        print(_summary(groups))
        print(f"plan: {plan_path}")
        if not args.apply:
            db.rollback()
            return 0

        resolutions = customer_merge.read_resolutions(args.resolutions) if args.resolutions else {}
        releases = customer_merge.read_releases(args.resolutions) if args.resolutions else set()
        run_id = f"dcu-{stamp}"
        companies = sorted({g.company_id for g in groups})
        merged = skipped = held = 0
        for cid in companies:
            try:
                result = customer_merge.apply(
                    db,
                    run_id=run_id,
                    company_id=cid,
                    codes=args.code,
                    resolutions=resolutions,
                    releases=releases,
                    actor_user_id=actor,
                )
                db.commit()
            except Exception:
                db.rollback()
                print(f"company {cid}: rolled back, nothing merged for it", file=sys.stderr)
                raise
            merged += len(result.merged)
            skipped += len(result.skipped)
            held += len(result.held)
            print(
                f"company {cid}: merged {len(result.merged)} groups, skipped {len(result.skipped)} "
                f"(conflicts), held {len(result.held)} (name mismatch)"
            )
        print(f"run id: {run_id}  merged: {merged}  skipped: {skipped}  held: {held}")

        if args.create_index:
            try:
                customer_merge.create_unique_index(db)
            except customer_merge.DuplicatesRemain as exc:
                db.rollback()
                print(f"index not created: {exc} (fill their `keep` column and re-run)")
                return 2
            db.commit()
            print(f"created {customer_merge.UNIQUE_INDEX}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
