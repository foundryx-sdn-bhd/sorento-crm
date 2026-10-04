"""Convert customer integration references from `<prefix>:<AutoKey>` to `<prefix>:<debtor code>`.

DEBTOR-CODE-LINK, card rule R9 (`documentation/plans/autocount/CARD-debtor-code-link-04oct.md`):
the owner ruled on 4 Oct 2026 that a debtor's identity is its company + debtor code, never the
AutoCount AutoKey. This one-off rewrites every existing customer link so none carries an AutoKey.

- The prefix of EACH ref is kept (field one is the source database name, which is data).
- Idempotent: a ref already equal to `<prefix>:<code>` is counted, never changed.
- Collision: when the new ref is already held in that company, the candidate is skipped; when
  several candidates map to one new ref, the oldest customer (created_at, then id) gets it.
- Apply writes a CSV of every change BEFORE updating; `--revert <csv>` puts the old values back.

A script, not an alembic migration, so a merge or deploy never rewrites prod links.

Usage (from sorento_crm_backend/):
    python -m scripts.convert_customer_refs_to_code                      # dry run, prints counts
    python -m scripts.convert_customer_refs_to_code --apply --csv out.csv
    python -m scripts.convert_customer_refs_to_code --revert out.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

# Allow `from app.*` imports when invoked from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal

CSV_HEADER = ["ref_id", "company_id", "old_source_ref", "new_source_ref"]

_CANDIDATES_SQL = text(
    """
    SELECT r.id::text AS ref_id, r.company_id::text AS company_id, r.source_ref AS old_ref,
           btrim(c.customer_code) AS code
    FROM integration_references r
    JOIN customers c ON c.id::text = r.entity_id AND c.company_id = r.company_id
    WHERE r.entity_type = 'customers' AND r.source_system = 'autocount'
      AND r.company_id IS NOT NULL
    ORDER BY c.created_at, c.id::text
    """
)

_HELD_SQL = text(
    """
    SELECT company_id::text, source_ref FROM integration_references
    WHERE entity_type = 'customers' AND source_system = 'autocount' AND company_id IS NOT NULL
    """
)


@dataclass(frozen=True)
class Change:
    ref_id: str
    company_id: str
    old_source_ref: str
    new_source_ref: str


@dataclass(frozen=True)
class Skip:
    ref_id: str
    old_source_ref: str
    reason: str  # "collision" | "unparseable"


@dataclass
class ConversionPlan:
    changes: list[Change] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    already_code: int = 0


def plan_conversion(db: Session) -> ConversionPlan:
    """What a conversion would change. Reads only."""
    plan = ConversionPlan()
    rows = db.execute(_CANDIDATES_SQL).mappings().all()
    # Every ref held right now counts as taken, including ones this run is about to change: a
    # new value that frees up only after another row's update is left to a second run, so no
    # UPDATE ever meets the unique index mid-way. Candidates come in oldest customer first, so
    # the first to claim a new ref is the oldest one.
    taken = {(cid, ref) for cid, ref in db.execute(_HELD_SQL).all()}
    pending: list[tuple[str, str, str, str]] = []
    for row in rows:
        old = row["old_ref"]
        if ":" not in old:
            plan.skipped.append(Skip(row["ref_id"], old, "unparseable"))
            continue
        new = f"{old.split(':', 1)[0]}:{row['code']}"
        if new == old:
            plan.already_code += 1
            continue
        pending.append((row["ref_id"], row["company_id"], old, new))
    for ref_id, company_id, old, new in pending:
        if (company_id, new) in taken:
            plan.skipped.append(Skip(ref_id, old, "collision"))
            continue
        taken.add((company_id, new))
        plan.changes.append(Change(ref_id, company_id, old, new))
    return plan


def apply_conversion(db: Session, plan: ConversionPlan, csv_path: str) -> int:
    """Write the CSV first, then rewrite each ref. Returns the number of refs changed."""
    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_HEADER)
        for c in plan.changes:
            writer.writerow([c.ref_id, c.company_id, c.old_source_ref, c.new_source_ref])
    count = 0
    for c in plan.changes:
        count += db.execute(
            text(
                "UPDATE integration_references SET source_ref = :new, updated_at = now() "
                "WHERE id::text = :id AND source_ref = :old"
            ),
            {"new": c.new_source_ref, "id": c.ref_id, "old": c.old_source_ref},
        ).rowcount
    return count


def revert(db: Session, csv_path: str) -> int:
    """Put back the old value of every ref the CSV names that still holds its new value."""
    count = 0
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            count += db.execute(
                text(
                    "UPDATE integration_references SET source_ref = :old, updated_at = now() "
                    "WHERE id::text = :id AND source_ref = :new"
                ),
                {"old": row["old_source_ref"], "id": row["ref_id"], "new": row["new_source_ref"]},
            ).rowcount
    return count


def _print_plan(plan: ConversionPlan) -> None:
    collisions = sum(1 for s in plan.skipped if s.reason == "collision")
    unparseable = sum(1 for s in plan.skipped if s.reason == "unparseable")
    print(
        f"to convert: {len(plan.changes)}  already code form: {plan.already_code}  "
        f"collisions: {collisions}  unparseable: {unparseable}"
    )
    for s in plan.skipped:
        print(f"  skipped {s.reason}: ref {s.ref_id} ({s.old_source_ref})")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="rewrite the refs (default: dry run)")
    mode.add_argument("--revert", metavar="CSV", help="restore the refs a previous --apply changed")
    parser.add_argument("--csv", help="where --apply writes its change list (required with --apply)")
    args = parser.parse_args(argv)
    if args.apply and not args.csv:
        parser.error("--apply needs --csv")

    db = SessionLocal()
    try:
        if args.revert:
            count = revert(db, args.revert)
            db.commit()
            print(f"reverted: {count}")
            return 0
        plan = plan_conversion(db)
        _print_plan(plan)
        if args.apply:
            count = apply_conversion(db, plan, args.csv)
            db.commit()
            print(f"converted: {count} (change list: {args.csv})")
        else:
            print("dry run: nothing changed (pass --apply --csv <path> to convert)")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
