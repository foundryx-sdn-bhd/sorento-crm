"""One-time load (and manual repair) of the chat analytics facts from the turn traces
(chat analytics CA-1, PLAN-chat-analytics-ca1-facts-09oct.md).

Writes a `chatbot.chat_ask_facts` row (plus its items) for every closed LIVE turn in the
range that has none. The live hook records each turn as it closes and the scheduler re-sweeps
the last three days nightly; this script exists for the first load, from the day the traces
begin, and for repairing a longer gap.

Idempotent: a turn that already has a fact is left alone, so a second run over the same range
writes nothing. Test turns and console turns are never facts.

Usage:
    python scripts/chat_facts_sweep.py --since 2026-09-05 [--until 2026-10-10]

Dates are Malaysia calendar days (`--until` exclusive). Goes through
`app.tasks.chat_turns.sweep_chat_facts`, the doorway into the chatbot package.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.database import SessionLocal
from app.models.base import set_company_scope
from app.tasks.chat_turns import sweep_chat_facts

_MALAYSIA = ZoneInfo("Asia/Kuala_Lumpur")


def _day(value: str) -> datetime:
    """Midnight of a Malaysia calendar day, as an aware datetime."""
    return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=_MALAYSIA)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--since", required=True, help="first Malaysia day to sweep, YYYY-MM-DD")
    parser.add_argument("--until", help="Malaysia day to stop before, YYYY-MM-DD (default: now)")
    args = parser.parse_args(argv)

    since = _day(args.since)
    until = _day(args.until) if args.until else None
    if until is not None and until <= since:
        parser.error("--until must be after --since")

    db = SessionLocal()
    # A script has no caller: read every company's contacts, as the scheduler does.
    set_company_scope(db, None)
    try:
        written = sweep_chat_facts(db, since=since, until=until)
    finally:
        db.close()
    print(f"chat facts: wrote {written} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
