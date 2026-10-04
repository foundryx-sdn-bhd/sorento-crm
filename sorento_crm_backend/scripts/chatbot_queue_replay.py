"""Replay the 1 Oct per-contact queue incident (contact 900000081) against a redis.

CHATBOT-QUEUE-FIX hand test. Drives `app.services.chatbot.dispatch` exactly as
`engine.run_turn` does (take + heartbeat, wait, release), with the prod timeline
compressed 10x and each turn's work replaced by a sleep. No database, no LLM, no
WhatsApp: a dry-run chat turn takes no ticket (engine H57), so the queue can only be
exercised directly.

    python -m scripts.chatbot_queue_replay            # uses settings.redis_url
    python -m scripts.chatbot_queue_replay --redis redis://localhost:6379/0

Prints one line per ticket and exits non-zero if any expectation fails.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import uuid

import redis as redis_lib

from app.services.chatbot import dispatch

SCALE = 0.1  # 10 prod seconds per real second
QUEUE_CAP_SECONDS = 30.0 * SCALE

# (label, arrival offset s, work s) in PROD seconds from ticket 1's arrival (10:05:35 MYT).
TIMELINE = [
    ("f0a2", 0.0, 6.0),
    ("65ce", 68.0, 5.0),
    # Was 232 s on prod (the parser hang). The parser call now stops at
    # `llm_call.CALL_DEADLINE_SECONDS` (35 s), so the turn is bounded near 40 s.
    ("a45f", 142.0, 40.0),
    ("a1d7", 150.0, 5.0),
    ("4a87", 249.0, 5.0),
    ("b34b", 255.0, 5.0),
    ("9a2c", 262.0, 5.0),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--redis", default=None)
    args = parser.parse_args()
    if args.redis:
        client = redis_lib.from_url(args.redis, decode_responses=True)
    else:
        from app.config import settings

        client = redis_lib.from_url(settings.redis_url, decode_responses=True)

    contact = f"ZZT-replay-{uuid.uuid4().hex[:8]}"
    # The earlier session's `done` outlived its `seq` (the quiet-hour expiry).
    client.set(dispatch.done_key(contact), 10, ex=60)

    rows: dict[str, dict] = {}
    running: set[int] = set()
    overlaps: list[str] = []
    lock = threading.Lock()
    t0 = time.monotonic()

    def turn(label: str, arrive: float, work: float) -> None:
        time.sleep(arrive * SCALE)
        ticket = dispatch.contact_ticket(client, contact)
        beat = dispatch.start_heartbeat(client, contact, ticket)
        arrived = time.monotonic() - t0
        timed_out = False
        try:
            try:
                dispatch.wait_for_turn(client, contact, ticket, timeout_s=QUEUE_CAP_SECONDS)
            except dispatch.QueueWait:
                timed_out = True
            started = time.monotonic() - t0
            with lock:
                earlier_live = sorted(t for t in running if t < ticket)
                if earlier_live and not timed_out:
                    overlaps.append(f"ticket {ticket} ran beside live {earlier_live}")
                running.add(ticket)
            time.sleep(work * SCALE)
        finally:
            beat.stop()
            dispatch.mark_done(client, contact, ticket)
            with lock:
                running.discard(ticket)
        rows[label] = {
            "ticket": ticket,
            "arrived": arrived,
            "wait": started - arrived,
            "timed_out": timed_out,
            "done_after": client.get(dispatch.done_key(contact)),
        }

    threads = [threading.Thread(target=turn, args=row) for row in TIMELINE]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"contact {contact}, cap {QUEUE_CAP_SECONDS:.1f}s (prod 30 s at 10x)")
    for label, *_ in TIMELINE:
        r = rows[label]
        print(
            f"{label} ticket {r['ticket']}: waited {r['wait']:.2f}s"
            f"{' TIMED OUT, ran anyway' if r['timed_out'] else ''}"
            f" done={r['done_after']}"
        )

    failures = []
    if rows["65ce"]["wait"] > 0.5:
        failures.append("65ce waited for a finished ticket 1")
    # a1d7 arrives 8 s after a45f starts its ~40 s turn, so it waits out the 30 s cap and
    # RUNS ANYWAY (its reply is never lost). Nothing else may time out.
    timed_out = [label for label, r in rows.items() if r["timed_out"]]
    if set(timed_out) - {"a1d7"}:
        failures.append(f"unexpected timeouts: {timed_out}")
    if len(rows) != len(TIMELINE):
        failures.append("a turn did not run")
    order = sorted(rows.values(), key=lambda r: r["ticket"])
    if [r["ticket"] for r in order] != list(range(1, len(TIMELINE) + 1)):
        failures.append("tickets were not 1..7 (seq did not restart at 1)")
    if overlaps:
        failures.extend(overlaps)
    if client.get(dispatch.done_key(contact)) != str(len(TIMELINE)):
        failures.append(f"final done {client.get(dispatch.done_key(contact))} != {len(TIMELINE)}")
    for key in client.scan_iter(f"chatbot:*:{contact}*"):
        client.delete(key)
    print("RESULT:", "PASS" if not failures else "FAIL " + "; ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
