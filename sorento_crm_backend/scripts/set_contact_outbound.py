#!/usr/bin/env python3
"""Flip the per-contact outbound kill switch (`respond_contacts.outbound_enabled`).

A disabled contact receives NOTHING outbound from Respond.io - no text, no
attachment, no template - regardless of which service tried to send it. Reads
are untouched. `--off --all` therefore silences every customer at once, which is
exactly why every mutating mode needs an explicit `--yes`.

Run from sorento_crm_backend/ AFTER `alembic upgrade head`:

    python scripts/set_contact_outbound.py --status [--contact <respond_io_id|phone|uuid>]
    python scripts/set_contact_outbound.py (--on|--off) (--all|--contact <ref>) [--dry-run] [--yes]

Examples:
    python scripts/set_contact_outbound.py --status
    python scripts/set_contact_outbound.py --off --all --dry-run
    python scripts/set_contact_outbound.py --off --all --yes
    python scripts/set_contact_outbound.py --on --contact 900000008 --yes

`--dry-run` reports what WOULD change and writes nothing. Without `--yes` a
mutating run behaves as a dry run and exits non-zero, so a half-typed command
can never silence anyone by accident.
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow `from app.*` imports when invoked from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.services.error_handler import AppException
from app.services.respond_outbound_service import (
    find_contact,
    set_all_outbound,
    set_contact_outbound,
    status_counts,
)

RULE = "=" * 72


def _banner(lines: list[str]) -> None:
    print(RULE)
    for line in lines:
        print(line)
    print(RULE)


def _describe(contact) -> str:
    label = contact.name or contact.phone_number or contact.id
    state = "ENABLED" if contact.outbound_enabled else "DISABLED"
    return f"{label} (respond_io_id={contact.respond_io_id or '-'}, phone={contact.phone_number}) -> {state}"


def _print_status(db, reference: str | None) -> int:
    if reference:
        contact = find_contact(db, reference)
        if contact is None:
            print(f"No Respond contact matches {reference!r}.")
            return 1
        print(_describe(contact))
        return 0
    counts = status_counts(db)
    _banner(
        [
            "OUTBOUND MESSAGING STATUS",
            f"  enabled  : {counts['enabled']}",
            f"  disabled : {counts['disabled']}",
            f"  total    : {counts['total']}",
        ]
    )
    return 0


def _run_all(db, *, enabled: bool, dry_run: bool) -> int:
    before = status_counts(db)
    verb = "ENABLE" if enabled else "DISABLE"
    _banner(
        [
            f"ABOUT TO {verb} OUTBOUND MESSAGING FOR **EVERY** CONTACT",
            f"  contacts        : {before['total']}",
            f"  currently enabled : {before['enabled']}",
            f"  currently disabled: {before['disabled']}",
            "  MODE            : DRY RUN (nothing will be written)"
            if dry_run
            else "  MODE            : LIVE (this writes to the database)",
        ]
    )

    affected = set_all_outbound(db, enabled=enabled, dry_run=dry_run)

    after = status_counts(db)
    _banner(
        [
            f"{'WOULD CHANGE' if dry_run else 'CHANGED'}: {affected} contact(s)",
            f"  enabled  : {after['enabled']}",
            f"  disabled : {after['disabled']}",
        ]
    )
    return 0


def _run_one(db, reference: str, *, enabled: bool, dry_run: bool) -> int:
    try:
        contact, changed = set_contact_outbound(
            db, reference, enabled=enabled, dry_run=dry_run
        )
    except AppException as exc:
        print(exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail))
        return 1

    verb = "ENABLE" if enabled else "DISABLE"
    if not changed:
        print(f"No change: {_describe(contact)}")
        return 0
    if dry_run:
        print(f"WOULD {verb}: {_describe(contact)} (dry run, nothing written)")
        return 0
    print(f"{verb}D: {_describe(contact)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--on", action="store_true", help="enable outbound messaging")
    mode.add_argument("--off", action="store_true", help="disable outbound messaging")
    mode.add_argument("--status", action="store_true", help="report only, never writes")

    target = parser.add_mutually_exclusive_group()
    target.add_argument("--all", action="store_true", help="every contact")
    target.add_argument(
        "--contact", metavar="REF", help="one contact: respond_io_id, phone number or internal id"
    )

    parser.add_argument(
        "--dry-run", action="store_true", help="report what WOULD change; write nothing"
    )
    parser.add_argument(
        "--yes", action="store_true", help="required to actually write (mutating modes)"
    )
    args = parser.parse_args()

    if not args.status and not (args.all or args.contact):
        parser.error("--on/--off needs a target: --all or --contact <ref>")

    # Without --yes a mutating run degrades to a dry run rather than doing nothing:
    # the operator still sees the effect, and still has to opt in to cause it.
    dry_run = args.dry_run or (not args.status and not args.yes)

    db = SessionLocal()
    try:
        if args.status:
            return _print_status(db, args.contact)

        enabled = bool(args.on)
        if args.all:
            rc = _run_all(db, enabled=enabled, dry_run=dry_run)
        else:
            rc = _run_one(db, args.contact, enabled=enabled, dry_run=dry_run)

        if dry_run and not args.dry_run:
            print("Refused to write: re-run with --yes to apply this change.")
            return 2
        return rc
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
