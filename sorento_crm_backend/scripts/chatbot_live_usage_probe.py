"""Run a few LIVE chatbot turns on a local / test copy and show what each one wrote to the usage
log and `analytics_connector.turn_costs` (CHAT-TURN-COST hand-test, crew audit 13 Oct).

Why not the chat console: a console turn is a test turn (`is_test`), and a test turn writes
nothing outside `chatbot.turns` by design (D14, `chatbot/usage.py`), so it can never produce a
usage row. This script builds LIVE envelopes for fresh ZZT contacts and calls `engine.run_turn`
in process, the way the queue worker does. Nothing is sent to WhatsApp: `run_turn` only RETURNS
its reply actions, and sending them is the injector's job, which is not called here. The parser
and clarifier call the copy's configured model for real, so the tokens are real.

Local / test copies only: it writes ZZT-marked contacts and live turns into the copy's database.

    venv/bin/python -m scripts.chatbot_live_usage_probe            # 6 turns, 2 fresh contacts
    venv/bin/python -m scripts.chatbot_live_usage_probe --turns 10

Exit code 0 when every turn that reached the parser has its usage row, 1 otherwise.

Quiet by default: the app's own log output is switched off for the run, because two messages
are expected and say nothing about usage rows. An embedding traceback about `OPENAI_API_KEY`
comes from the embedding worker on a copy with no embedding key. A `RespondContact not found`
404 comes from a business-lane tool looking up a CRM link that a fresh probe contact does not
have. `--verbose` shows the full log.
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from typing import Any, Callable

from sqlalchemy import text

MESSAGES = (
    "hi",
    "do you have stock for SRTWC8517?",
    "what is the status of my last order?",
    "thanks!",
    "price for brake pads for a Myvi",
    "ok noted",
    "any promotion this month?",
    "can I speak to someone?",
    "show my outstanding invoices",
    "good morning",
)


def _envelope(contact_rid: str, message_id: str, body: str):
    from app.services.chatbot.contracts import Envelope

    return Envelope(
        contact={"id": contact_rid, "firstName": "ZZT probe", "custom_fields": []},
        message={
            "event_type": "message.received",
            "contact": {"id": contact_rid},
            "message": {
                "messageId": message_id,
                "contactId": contact_rid,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": body},
            },
        },
    )


def _new_contact(db) -> tuple[str, str]:
    tag = uuid.uuid4().hex[:8]
    cid, rid = f"ZZT-ctc-probe-{tag}", f"zzt-ctc-probe-{tag}"
    db.execute(
        text("INSERT INTO respond_contacts (id, phone_number, name, respond_io_id, chatbot_allowed) "
             "VALUES (:i, :p, :n, :r, true)"),
        {"i": cid, "p": f"+6010000{uuid.uuid4().int % 10000:04d}", "n": f"ZZT CTC probe {tag}", "r": rid},
    )
    db.commit()
    return cid, rid


def run_probe(session_factory: Callable[[], Any], turns: int = 6) -> list[dict]:
    """Run ``turns`` live turns over two fresh contacts; one result dict per turn."""
    from app.models.ai_assistant import AIAssistantUsageLog
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import engine

    db = session_factory()
    try:
        contacts = [_new_contact(db), _new_contact(db)]
    finally:
        db.close()
    out = []
    for i in range(turns):
        _cid, rid = contacts[i % 2]
        body = MESSAGES[i % len(MESSAGES)]
        result = engine.run_turn(_envelope(rid, f"zzt-ctc-probe-msg-{uuid.uuid4().hex}", body),
                                 session_factory=session_factory)
        db = session_factory()
        try:
            row = db.get(ChatbotTurn, result.turn_id)
            calls = (db.query(AIAssistantUsageLog)
                     .filter(AIAssistantUsageLog.chatbot_turn_id == str(result.turn_id)).all())
            stages = [r.get("stage") for r in (row.trace or []) if isinstance(r, dict)]
            out.append({
                "turn_id": str(result.turn_id), "text": body, "status": row.status, "stage": row.stage,
                "reached_parser": "understood" in stages, "is_test": row.is_test,
                "calls": [(c.feature, c.model, c.prompt_tokens, c.cached_tokens, c.completion_tokens) for c in calls],
            })
        finally:
            db.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--turns", type=int, default=6)
    ap.add_argument("--verbose", action="store_true", help="show the app's log output too")
    args = ap.parse_args(argv)
    if not args.verbose:
        # See the module docstring: the expected noise is log output, not a failure.
        logging.disable(logging.CRITICAL)

    from app.database import SessionLocal

    results = run_probe(SessionLocal, args.turns)
    db = SessionLocal()
    try:
        costs = {r[0]: r[1:] for r in db.execute(
            text("SELECT id, cost_usd, priced FROM analytics_connector.turn_costs WHERE id = ANY(:i)"),
            {"i": [r["turn_id"] for r in results]},
        )}
    finally:
        db.close()
    ok = True
    print("| turn_id | text | status | reached parser | usage rows (feature, model, prompt, cached, completion) | turn_costs cost_usd / priced |")
    print("| --- | --- | --- | --- | --- | --- |")
    for r in results:
        missing = r["reached_parser"] and not any(c[0] == "chatbot_parser" for c in r["calls"])
        ok &= not missing and not r["is_test"]
        cost = costs.get(r["turn_id"], ("(no row)", ""))
        print(f"| {r['turn_id']} | {r['text']} | {r['status']}/{r['stage']} | {r['reached_parser']} | "
              f"{r['calls'] or 'NONE'}{' MISSING' if missing else ''} | {cost[0]} / {cost[1]} |")
    if not args.verbose:
        print("\n(app log output hidden; --verbose shows it, including the expected OPENAI_API_KEY embedding "
              "traceback and RespondContact-not-found 404)")
    print("\nPASS: every live turn that reached the parser wrote its usage row" if ok
          else "\nFAIL: a live turn that reached the parser has no usage row")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
