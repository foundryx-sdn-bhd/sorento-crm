"""Run the chat topic labeller now, in this process (chat analytics CA-2,
PLAN-chat-analytics-ca2-taxonomy-09oct.md).

Labels every ask with no topic, or with a taxonomy version older than the current one, newest
first, in batches of 40 through the configured LLM. The nightly job does the same on the worker
when CHAT_TOPIC_LABELLER_ENABLED is set; this script is for the first run and for a dev copy
(owner, 9 Oct 2026: labelling on dev only for now). It calls the provider, so it costs tokens:
about 200 input tokens per ask.

Usage:
    python scripts/chat_topic_label.py [--limit 400]

Goes through `app.tasks.chat_turns.label_chat_topics_job`, the doorway into the chatbot package.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, help="label at most this many asks (default: all due)")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")

    from app.tasks.chat_turns import label_chat_topics_job

    print(f"chat topic labeller: {label_chat_topics_job(limit=args.limit)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
