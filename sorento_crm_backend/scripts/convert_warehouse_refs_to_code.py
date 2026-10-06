"""Convert warehouse integration references from `<db>:<AutoKey>` to `<db>:<warehouse_code>`.

WH-REF-CONVERT (owner, 6 Oct 2026): the AutoCount warehouses feed now keys a warehouse by its
code, so it sends `source_ref = '<db>:<warehouse_code>'`. The ~80 prod rows linked between
6 Sep and 1 Oct still carry the old `'<db>:<AutoKey>'` form, so `_resolve_ref` misses, the
ingest adopts the row by code, finds the old link on it and fails the record with
"warehouse_code='...' is already linked to another source"
(`app/services/master_ingest_service.py`, `_apply_scoped`, the `ReferenceConflict` branch).
Rewriting the stored ref to the code form makes the next push a plain ref hit -> `updated`.

Modelled on `scripts/convert_customer_refs_to_code.py` (DEBTOR-CODE-LINK R9):

- Only `entity_type = 'warehouses'` and `source_system = 'autocount'` rows are ever read or
  written. Customers, products, suppliers, agents, documents: untouched.
- The prefix of EACH ref is kept (field one is the source database name, which is data).
- Idempotent: a ref already equal to `<prefix>:<code>` is counted, never changed.
- The dry run (default) lists every candidate per company with the warehouse code it maps to,
  and FLAGS: a ref whose warehouse no longer exists (`missing_warehouse`), one whose warehouse
  sits in another company (`company_mismatch`), a new ref already held in that company or
  claimed earlier in the same run (`collision`), a ref without a colon (`unparseable`) and a
  blank warehouse code (`blank_code`). A flagged ref is never rewritten.
- `--apply` refuses while anything is flagged unless `--force`, which converts the clean refs
  and leaves the flagged ones as they are (reported again). Apply writes a CSV backup of every
  change BEFORE updating, in one transaction; `--revert <csv>` puts the old values back.
  A planned ref that moved between plan and UPDATE (the feed, running concurrently) aborts
  the run and rolls every update back, never a silently partial conversion.

A script, not an alembic migration, so a merge or deploy never rewrites prod links.

Usage (from sorento_crm_backend/):
    python -m scripts.convert_warehouse_refs_to_code                        # dry run
    python -m scripts.convert_warehouse_refs_to_code --apply --csv out.csv  # convert
    python -m scripts.convert_warehouse_refs_to_code --apply --force --csv out.csv
    python -m scripts.convert_warehouse_refs_to_code --revert out.csv       # restore
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

# Allow `from app.*` imports when invoked from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal

CSV_HEADER = ["ref_id", "company_id", "entity_id", "old_source_ref", "new_source_ref"]

ENTITY_TYPE = "warehouses"
SOURCE_SYSTEM = "autocount"

# Every warehouse ref, with its warehouse (if any) and its company label. LEFT JOINs so a
# dangling or cross-company ref is reported rather than silently dropped. Oldest warehouse
# first so, when two candidates map to one new ref, the oldest one claims it.
_CANDIDATES_SQL = text(
    """
    SELECT r.id::text AS ref_id, r.company_id::text AS company_id, r.entity_id AS entity_id,
           r.source_ref AS old_ref,
           w.id::text AS warehouse_id, w.company_id::text AS warehouse_company_id,
           btrim(w.warehouse_code) AS code,
           c.code AS company_code, c.name AS company_name
    FROM integration_references r
    LEFT JOIN warehouses w ON w.id::text = r.entity_id
    LEFT JOIN companies c ON c.id = r.company_id
    WHERE r.entity_type = :entity_type AND r.source_system = :source_system
    ORDER BY c.code NULLS LAST, w.created_at NULLS LAST, w.id::text, r.id::text
    """
)

_HELD_SQL = text(
    """
    SELECT company_id::text, source_ref FROM integration_references
    WHERE entity_type = :entity_type AND source_system = :source_system
    """
)

# Guarded by id AND entity type AND source system AND the current value, so neither a run
# nor a stale / edited backup CSV can reach any other row, and a re-run matches nothing.
_UPDATE_SQL = text(
    "UPDATE integration_references SET source_ref = :new, updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND entity_type = :entity_type "
    "AND source_system = :source_system AND source_ref = :old"
)


class ConversionBlocked(RuntimeError):
    """`--apply` while a ref is flagged and `--force` was not given. Nothing was written."""


class ConversionDrift(RuntimeError):
    """A planned ref no longer held its planned old value when the UPDATE ran (the feed
    moved it between plan and apply; planning takes no locks). Raised before commit so the
    caller rolls the whole run back instead of reporting a partial conversion."""


@dataclass(frozen=True)
class Change:
    ref_id: str
    company_id: str
    company_label: str
    entity_id: str
    warehouse_code: str
    old_source_ref: str
    new_source_ref: str


@dataclass(frozen=True)
class Skip:
    ref_id: str
    company_id: Optional[str]
    company_label: str
    old_source_ref: str
    reason: str  # "collision" | "missing_warehouse" | "company_mismatch" | "unparseable" | "blank_code"
    detail: str = ""


@dataclass
class ConversionPlan:
    changes: list[Change] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    already_code: int = 0
    # per company label: Counter of ref prefixes already in code form, so the operator can
    # compare the stored `<db>` with the one the feed sends before applying.
    code_form_prefixes: dict[str, Counter] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        """True when any ref is flagged. `--apply` refuses in that state unless forced."""
        return bool(self.skipped)


def _company_label(row) -> str:
    code = row["company_code"] or "?"
    return f"{code} ({row['company_id']})"


def plan_conversion(db: Session) -> ConversionPlan:
    """What a conversion would change, and what it refuses to touch. Reads only."""
    plan = ConversionPlan()
    params = {"entity_type": ENTITY_TYPE, "source_system": SOURCE_SYSTEM}
    rows = db.execute(_CANDIDATES_SQL, params).mappings().all()
    # Every ref held right now counts as taken, including ones this run is about to change: a
    # new value that frees up only after another row's update is left to a second run, so no
    # UPDATE ever meets the unique index mid-way.
    taken = {(cid, ref) for cid, ref in db.execute(_HELD_SQL, params).all()}
    pending: list[tuple[str, str, str, str, str, str, str]] = []
    for row in rows:
        old = row["old_ref"]
        label = _company_label(row)
        if row["warehouse_id"] is None:
            plan.skipped.append(
                Skip(row["ref_id"], row["company_id"], label, old, "missing_warehouse",
                     f"entity_id {row['entity_id']} has no warehouses row")
            )
            continue
        if row["warehouse_company_id"] != row["company_id"]:
            plan.skipped.append(
                Skip(row["ref_id"], row["company_id"], label, old, "company_mismatch",
                     f"warehouse {row['warehouse_id']} belongs to company "
                     f"{row['warehouse_company_id']}")
            )
            continue
        if ":" not in old:
            plan.skipped.append(Skip(row["ref_id"], row["company_id"], label, old, "unparseable"))
            continue
        if not row["code"]:
            plan.skipped.append(Skip(row["ref_id"], row["company_id"], label, old, "blank_code"))
            continue
        prefix = old.split(":", 1)[0]
        new = f"{prefix}:{row['code']}"
        if new == old:
            plan.already_code += 1
            plan.code_form_prefixes.setdefault(label, Counter())[prefix] += 1
            continue
        pending.append((row["ref_id"], row["company_id"], label, row["warehouse_id"], row["code"], old, new))
    for ref_id, company_id, label, warehouse_id, code, old, new in pending:
        if (company_id, new) in taken:
            plan.skipped.append(
                Skip(ref_id, company_id, label, old, "collision", f"{new} is already held")
            )
            continue
        taken.add((company_id, new))
        plan.changes.append(Change(ref_id, company_id, label, warehouse_id, code, old, new))
    return plan


def apply_conversion(db: Session, plan: ConversionPlan, csv_path: str, *, force: bool = False) -> int:
    """Write the CSV backup first, then rewrite each ref. Returns the number of refs changed.

    Raises `ConversionBlocked` before touching anything (the CSV included) when the plan
    carries a flag and `force` is False. Commit is the caller's, so the whole run is one
    transaction.
    """
    if plan.blocked and not force:
        raise ConversionBlocked(
            f"{len(plan.skipped)} flagged ref(s); re-run with --force to convert the clean ones"
        )
    # "x": never replace an earlier run's backup, which may be the only way back.
    with open(csv_path, "x", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_HEADER)
        for c in plan.changes:
            writer.writerow([c.ref_id, c.company_id, c.entity_id, c.old_source_ref, c.new_source_ref])
    count = 0
    for c in plan.changes:
        hit = db.execute(
            _UPDATE_SQL,
            {
                "new": c.new_source_ref,
                "id": c.ref_id,
                "entity_type": ENTITY_TYPE,
                "source_system": SOURCE_SYSTEM,
                "old": c.old_source_ref,
            },
        ).rowcount
        if hit != 1:
            raise ConversionDrift(
                f"ref {c.ref_id} no longer holds {c.old_source_ref!r}; re-run the dry run"
            )
        count += hit
    return count


def revert(db: Session, csv_path: str) -> int:
    """Put back the old value of every ref the CSV names that still holds its new value."""
    count = 0
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            count += db.execute(
                _UPDATE_SQL,
                {
                    "new": row["old_source_ref"],
                    "id": row["ref_id"],
                    "entity_type": ENTITY_TYPE,
                    "source_system": SOURCE_SYSTEM,
                    "old": row["new_source_ref"],
                },
            ).rowcount
    return count


def _print_plan(plan: ConversionPlan) -> None:
    by_company: dict[str, list[Change]] = {}
    for c in plan.changes:
        by_company.setdefault(c.company_label, []).append(c)
    labels = sorted(set(by_company) | set(plan.code_form_prefixes) | {s.company_label for s in plan.skipped})
    for label in labels:
        changes = by_company.get(label, [])
        prefixes = plan.code_form_prefixes.get(label, Counter())
        flagged = [s for s in plan.skipped if s.company_label == label]
        print(
            f"company {label}: to convert {len(changes)}, already code form "
            f"{sum(prefixes.values())}, flagged {len(flagged)}"
        )
        if prefixes:
            held = ", ".join(f"{p}={n}" for p, n in sorted(prefixes.items()))
            print(f"  code-form prefixes already stored: {held}")
        for c in changes:
            print(f"  {c.old_source_ref} -> {c.new_source_ref}  (warehouse {c.warehouse_code}, ref {c.ref_id})")
        for s in flagged:
            detail = f": {s.detail}" if s.detail else ""
            print(f"  FLAG {s.reason}: ref {s.ref_id} ({s.old_source_ref}){detail}")
    reasons = Counter(s.reason for s in plan.skipped)
    print(
        f"to convert: {len(plan.changes)}  already code form: {plan.already_code}  "
        f"collisions: {reasons['collision']}  missing warehouse: {reasons['missing_warehouse']}  "
        f"company mismatch: {reasons['company_mismatch']}  unparseable: {reasons['unparseable']}  "
        f"blank code: {reasons['blank_code']}"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="list what would change (the default)")
    mode.add_argument("--apply", action="store_true", help="rewrite the refs")
    mode.add_argument("--revert", metavar="CSV", help="restore the refs a previous --apply changed")
    parser.add_argument("--csv", help="where --apply writes its backup (required with --apply)")
    parser.add_argument(
        "--force", action="store_true",
        help="with --apply: convert the clean refs even though some are flagged",
    )
    args = parser.parse_args(argv)
    if args.apply and not args.csv:
        parser.error("--apply needs --csv")
    if args.force and not args.apply:
        parser.error("--force only means something with --apply")

    db = SessionLocal()
    try:
        if args.revert:
            count = revert(db, args.revert)
            db.commit()
            print(f"reverted: {count}")
            return 0
        plan = plan_conversion(db)
        _print_plan(plan)
        if not args.apply:
            print("dry run: nothing changed (pass --apply --csv <path> to convert)")
            return 0
        try:
            count = apply_conversion(db, plan, args.csv, force=args.force)
        except ConversionBlocked as exc:
            db.rollback()
            print(f"refused: {exc}; nothing changed, no backup written")
            return 2
        except ConversionDrift as exc:
            db.rollback()
            print(f"aborted: {exc}; every update of this run was rolled back (backup: {args.csv})")
            return 3
        db.commit()
        print(f"converted: {count} (backup: {args.csv})")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
