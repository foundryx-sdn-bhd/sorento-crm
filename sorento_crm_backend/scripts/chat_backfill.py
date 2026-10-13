"""Backfill chat facts and topic labels for one era of old messages, in this process (chat
analytics CA-4, PLAN-chat-analytics-ca4-backfill-09oct.md).

Eras (Malaysia dates): A 17 Apr to 21 Jul, B 22 Jul to 18 Aug, C 19 Aug to 4 Sep, D the 5 to
22 Sep messages the bot never traced. Reads each era's incoming chat history, links the reply,
and labels batches of 40 through the configured LLM; a rerun picks up only what is left.
Every run is a row in chatbot.chat_backfill_runs (counts and tokens).

It calls the provider, so it costs tokens; run --estimate first (no model call, no write).
Dev only; the production run is the owner's.

Usage:
    python scripts/chat_backfill.py --era A --estimate
    python scripts/chat_backfill.py --era A --limit 80
    python scripts/chat_backfill.py --era A --relabel     # after a taxonomy version bump

Goes through `app.tasks.chat_turns.backfill_chat_history_job`, the doorway into the chatbot
package.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ERAS = ("A", "B", "C", "D")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--era", required=True, choices=ERAS, help="the era to read")
    parser.add_argument("--estimate", action="store_true", help="count and project tokens only; no model call")
    parser.add_argument("--limit", type=int, help="read at most this many messages (default: all left)")
    parser.add_argument("--relabel", action="store_true", help="also re-read messages labelled under an older taxonomy")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")

    from app.tasks.chat_turns import backfill_chat_history_job

    result = backfill_chat_history_job(args.era, limit=args.limit, estimate=args.estimate, relabel=args.relabel)
    print(f"chat history backfill: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
