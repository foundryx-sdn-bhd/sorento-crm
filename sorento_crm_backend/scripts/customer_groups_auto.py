#!/usr/bin/env python3
"""Fill customer groups automatically by the ledger name rule (CUST-GROUP-AUTO).

Owner, 5 Oct 2026: "I need script to set automatically, not sheet". This overrides the 3 Oct
"no automatic name-matching" ruling for THIS script only; nothing runs it on deploy or upgrade.

The rule is the one the dev seed used (`cust_group_0001` in ec2dd5b79, removed by #1451):

- Per company, ledgers sharing `ledger_family_key(customer_name)` are one family.
- A family becomes a group when it has 2+ ledgers and at least one numbered account
  (`account_level` set). CASH / SHOPEE / LAZADA families never do.
- The group is named after the member with the lowest (account level, code): its name without
  the bracketed runs that tell the ledgers apart, keeping those every member shares
  (`CHENG HUAT HARDWARE (SENTUL) SDN BHD`).

Safety:

- Only ledgers with no group are written. A family where any ledger already has a group is
  skipped whole, so an office edit is never overridden and a re-run changes nothing.
- A group already named the same in that company (case-insensitive) is reused, not duplicated.
- One debtor code (company + lower(btrim(code))) never ends up in two groups: PR #1475 merges
  those rows and treats two different groups as a conflict. A family that would cause that is
  skipped and listed. `301-*` cash codes are exempt, as in #1475.

Run from the backend root (`/app` in the container):

    python scripts/customer_groups_auto.py --db-url "$DATABASE_URL" --out /tmp/cga
    python scripts/customer_groups_auto.py --db-url "$DATABASE_URL" --out /tmp/cga --apply --actor-email ops@example.com

The dry run (default) prints counts and writes the plan CSV (real customer names: keep it out
of the repo). `--apply` writes everything in ONE transaction, with an audit row per group
created and per ledger assigned, all carrying the run id as `correlation_id`.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa  # noqa: E402

from app.services.ledger_family import _marker_level, ledger_family_key  # noqa: E402

#: Families whose trading name starts with one of these are channels, not one customer.
EXCLUDED_FIRST_WORDS = frozenset({"CASH", "SHOPEE", "LAZADA"})

#: Debtor codes #1475 never treats a group difference on as a conflict.
CASH_CODE_PREFIXES = ("301-",)

#: (id, company_id, customer_code, customer_name, account_level, customer_group_id)
Row = Sequence


@dataclass
class PlannedGroup:
    company_id: str
    name: str
    member_ids: list[str]
    codes: list[str]
    names: list[str]
    #: the existing group of the same (company, lower(name)) to reuse, else None (created)
    existing_group_id: str | None = None
    reason: str = "name_family"


@dataclass
class Skipped:
    company_id: str
    name: str
    codes: list[str]
    names: list[str]
    reason: str  # already_grouped | debtor_code_conflict


@dataclass
class CodeConflict:
    company_id: str
    code: str
    targets: list[str]


@dataclass
class Plan:
    groups: list[PlannedGroup] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    conflicts: list[CodeConflict] = field(default_factory=list)


def _bracket_runs(text: str) -> list[str]:
    """The top-level bracketed or parenthesised runs of `text`, each whole, upper-cased."""
    runs: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in text:
        if ch in "[(":
            if depth == 0:
                cur = []
            depth += 1
        if depth:
            cur.append(ch)
        if ch in "])" and depth:
            depth -= 1
            if depth == 0:
                runs.append(" ".join("".join(cur).upper().split()))
    return runs


def shared_bracket_label(names: Sequence[str]) -> str:
    """The first name with every bracketed run dropped EXCEPT those every name carries
    (`(SENTUL)`, `(M)`); an `[A/C n]` marker is a ledger's, never the company's name."""
    names = [n for n in names if n]
    if not names:
        return ""
    shared = set(_bracket_runs(names[0]))
    for name in names[1:]:
        shared &= set(_bracket_runs(name))
    shared = {r for r in shared if _marker_level(r[1:-1].strip()) is None}
    out: list[str] = []
    depth = 0
    run: list[str] = []
    for ch in names[0]:
        if ch in "[(":
            if depth == 0:
                run = []
            depth += 1
        if depth:
            run.append(ch)
            if ch in "])":
                depth -= 1
                if depth == 0:
                    text = "".join(run)
                    if " ".join(text.upper().split()) in shared:
                        out.append(text)
            continue
        out.append(ch)
    cleaned = " ".join("".join(out).split()).strip().strip("-").strip()
    return cleaned or names[0]


def code_key(code: str | None) -> str:
    return (code or "").strip().lower()


def _lead_order(row: Row):
    level = row[4]
    return (level if level is not None else 1 << 30, row[2] or "")


def plan(rows: Iterable[Row], existing_groups: dict[tuple[str, str], str]) -> Plan:
    """The groups to create or reuse. Pure: `rows` are customers, `existing_groups` maps
    (company_id, lower(group name)) -> group id."""
    rows = [tuple(r) for r in rows]
    families: dict[tuple[str, str], list] = {}
    for row in rows:
        name = row[3]
        key = ledger_family_key(name) if name else ""
        if key and key.split()[0] not in EXCLUDED_FIRST_WORDS:
            families.setdefault((str(row[1]), key), []).append(row)

    result = Plan()
    candidates: list[PlannedGroup] = []
    for (company_id, _key), members in families.items():
        if len(members) < 2 or all(m[4] is None for m in members):
            continue
        members = sorted(members, key=_lead_order)
        label = shared_bracket_label([m[3] for m in members])
        codes = [m[2] or "" for m in members]
        names = [m[3] for m in members]
        if any(m[5] is not None for m in members):
            result.skipped.append(Skipped(company_id, label, codes, names, "already_grouped"))
            continue
        candidates.append(
            PlannedGroup(
                company_id=company_id,
                name=label,
                member_ids=[str(m[0]) for m in members],
                codes=codes,
                names=names,
                existing_group_id=existing_groups.get((company_id, label.lower())),
            )
        )

    # One debtor code, one group (#1475). Drop the families that would split a code, repeat
    # until stable (dropping one can only remove targets).
    by_code: dict[tuple[str, str], list] = {}
    for row in rows:
        key = code_key(row[2])
        if key and not key.startswith(CASH_CODE_PREFIXES):
            by_code.setdefault((str(row[1]), key), []).append(row)
    by_code = {k: v for k, v in by_code.items() if len(v) > 1}

    conflicts: dict[tuple[str, str], CodeConflict] = {}
    while True:
        target: dict[str, str] = {}
        owner: dict[str, PlannedGroup] = {}
        for g in candidates:
            for mid in g.member_ids:
                target[mid] = "planned:" + g.name.lower()
                owner[mid] = g
        dropped: list[PlannedGroup] = []
        for (company_id, key), members in by_code.items():
            targets = set()
            for m in members:
                if m[5] is not None:
                    targets.add("group:" + str(m[5]))
                elif str(m[0]) in target:
                    targets.add(target[str(m[0])])
            if len(targets) > 1:
                conflicts[(company_id, key)] = CodeConflict(company_id, key, sorted(targets))
                for m in members:
                    g = owner.get(str(m[0]))
                    if g is not None and g not in dropped:
                        dropped.append(g)
        if not dropped:
            break
        for g in dropped:
            candidates.remove(g)
            result.skipped.append(Skipped(g.company_id, g.name, g.codes, g.names, "debtor_code_conflict"))

    result.groups = sorted(candidates, key=lambda g: (g.company_id, g.name.lower()))
    result.conflicts = sorted(conflicts.values(), key=lambda c: (c.company_id, c.code))
    return result


def load_plan(conn) -> Plan:
    rows = conn.execute(
        sa.text(
            "SELECT id::text, company_id::text, customer_code, customer_name, account_level, "
            "customer_group_id::text FROM customers WHERE customer_name IS NOT NULL"
        )
    ).fetchall()
    existing = {
        (str(c), str(n).lower()): str(i)
        for i, c, n in conn.execute(sa.text("SELECT id::text, company_id::text, name FROM customer_groups"))
    }
    return plan(rows, existing)


def _audit(conn, *, entity_type, entity_id, action, actor, company_id, new, description, event, run_id):
    conn.execute(
        sa.text(
            """
            INSERT INTO audit_logs
                (id, entity_type, entity_id, action, user_id, company_id, new_values, description,
                 actor_type, source, root_entity_type, root_entity_id, event, correlation_id)
            VALUES (gen_random_uuid(), :t, :i, :a, :u, :c, CAST(:new AS jsonb), :d,
                    'user', 'script', :t, :i, :e, :run)
            """
        ),
        {
            "t": entity_type,
            "i": entity_id,
            "a": action,
            "u": actor,
            "c": company_id,
            "new": json.dumps(new),
            "d": description,
            "e": event,
            "run": run_id,
        },
    )


def apply(conn, the_plan: Plan, *, actor_user_id: str, run_id: str) -> tuple[int, int]:
    """Write `the_plan` on `conn` (the caller owns the transaction). Returns
    (groups created, ledgers assigned). Only ledgers still without a group are written."""
    created = assigned = 0
    for g in the_plan.groups:
        group_id = g.existing_group_id or conn.execute(
            sa.text("SELECT id::text FROM customer_groups WHERE company_id = :c AND lower(name) = lower(:n)"),
            {"c": g.company_id, "n": g.name},
        ).scalar()
        if group_id is None:
            group_id = str(uuid.uuid4())
            conn.execute(
                sa.text(
                    "INSERT INTO customer_groups (id, company_id, name, created_at, updated_at) "
                    "VALUES (:i, :c, :n, now(), now())"
                ),
                {"i": group_id, "c": g.company_id, "n": g.name},
            )
            created += 1
            _audit(
                conn,
                entity_type="customer_group",
                entity_id=group_id,
                action="CREATE",
                actor=actor_user_id,
                company_id=g.company_id,
                new={"name": g.name, "run_id": run_id},
                description=f"Customer group {g.name} created by the name rule",
                event="customer_group.auto_create",
                run_id=run_id,
            )
        updated = conn.execute(
            sa.text(
                "UPDATE customers SET customer_group_id = :g, updated_at = now() "
                "WHERE id = ANY(CAST(:ids AS uuid[])) AND customer_group_id IS NULL RETURNING id::text"
            ),
            {"g": group_id, "ids": g.member_ids},
        ).scalars().all()
        for cid in updated:
            _audit(
                conn,
                entity_type="customer",
                entity_id=cid,
                action="UPDATE",
                actor=actor_user_id,
                company_id=g.company_id,
                new={"customer_group_id": group_id, "customer_group_name": g.name, "run_id": run_id},
                description=f"Customer group set to {g.name} by the name rule",
                event="customer.group_auto_assign",
                run_id=run_id,
            )
        assigned += len(updated)
    return created, assigned


def write_plan_csv(the_plan: Plan, out_dir, stamp: str | None = None) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"customer-groups-plan-{stamp}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["company", "group_name", "action", "ledger_count", "ledger_codes", "ledger_names", "reason"])
        for g in the_plan.groups:
            w.writerow(
                [g.company_id, g.name, "reuse" if g.existing_group_id else "create", len(g.member_ids),
                 "; ".join(g.codes), "; ".join(g.names), g.reason]
            )
        for s in the_plan.skipped:
            w.writerow([s.company_id, s.name, "skip", len(s.codes), "; ".join(s.codes), "; ".join(s.names), s.reason])
        for c in the_plan.conflicts:
            w.writerow([c.company_id, "", "conflict", "", c.code, "; ".join(c.targets), "debtor_code_conflict"])
    return path


def _summary(the_plan: Plan) -> str:
    reuse = sum(1 for g in the_plan.groups if g.existing_group_id)
    by_reason: dict[str, int] = {}
    for s in the_plan.skipped:
        by_reason[s.reason] = by_reason.get(s.reason, 0) + 1
    return (
        f"groups: {len(the_plan.groups)} (create {len(the_plan.groups) - reuse}, reuse {reuse})  "
        f"ledgers: {sum(len(g.member_ids) for g in the_plan.groups)}\n"
        f"skipped families: {len(the_plan.skipped)} {by_reason}  "
        f"debtor-code conflicts (#1475): {len(the_plan.conflicts)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-url", required=True, help="the database to read (and with --apply, write)")
    parser.add_argument("--out", default="/tmp/customer-groups-auto", help="where the plan CSV goes")
    parser.add_argument("--apply", action="store_true", help="write the plan in one transaction")
    parser.add_argument("--actor-email", help="required with --apply: the user named on every audit row")
    args = parser.parse_args(argv)
    if args.apply and not args.actor_email:
        parser.error("--apply needs --actor-email")

    engine = sa.create_engine(args.db_url)
    with engine.begin() as conn:
        the_plan = load_plan(conn)
        path = write_plan_csv(the_plan, args.out)
        print(_summary(the_plan))
        print(f"plan CSV: {path}")
        if not args.apply:
            print("dry run: nothing written (add --apply --actor-email <email> to write)")
            return 0
        actor = conn.execute(
            sa.text("SELECT id::text FROM users WHERE lower(email) = lower(:e)"), {"e": args.actor_email}
        ).scalar()
        if actor is None:
            raise SystemExit(f"unknown user {args.actor_email!r}")
        run_id = "cga-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        created, assigned = apply(conn, the_plan, actor_user_id=actor, run_id=run_id)
        print(f"applied run {run_id}: groups created {created}, ledgers assigned {assigned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
