"""Simulate WhatsApp round-trip scenarios and show what the watchdog reports.

Verification aid for UAC OBS-S4-05..S4-18. Synthesizes turns directly into
`chat_histories` inside a transaction that is always rolled back - the DB is untouched.

    venv/bin/python scripts/simulate_chat_latency.py

Each scenario is evaluated through the same code path the scheduled task uses, so what
prints here is what an on-call admin would actually receive.
"""
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models.chat_history import ChatHistory
from app.models.user import SystemSetting
from app.services import chat_latency_service as latency
from app.services.system_health_alert_service import _eval_chat_latency

CONTACT = "sim-900000063"


def _msg(db, *, type, turn_id, respond_ts, ingest_at=None, delivery_status=None):
    db.add(
        ChatHistory(
            channel="whatsapp",
            contact_id=CONTACT,
            phone_number="+60100000000",
            message="simulated",
            sent_at=respond_ts,
            type=type,
            turn_id=turn_id,
            respond_ts=respond_ts,
            ingest_at=ingest_at,
            delivery_status=delivery_status,
        )
    )


def _clear(db):
    db.query(ChatHistory).filter(ChatHistory.contact_id == CONTACT).delete()
    db.flush()


def _scenario_healthy(db, now, n=40):
    for i in range(n):
        t = now - timedelta(minutes=i + 1)
        _msg(db, type="incoming", turn_id=f"h{i}", respond_ts=t)
        _msg(db, type="outgoing", turn_id=f"h{i}", respond_ts=t + timedelta(seconds=3))


def _scenario_degraded(db, now, n=40):
    for i in range(n):
        t = now - timedelta(minutes=i + 1)
        _msg(db, type="incoming", turn_id=f"d{i}", respond_ts=t)
        _msg(db, type="outgoing", turn_id=f"d{i}", respond_ts=t + timedelta(seconds=25))


def _scenario_one_stalled(db, now):
    """40 healthy turns plus a single 90s turn - p99 barely moves, ceiling must catch it."""
    _scenario_healthy(db, now)
    t = now - timedelta(minutes=2)
    _msg(db, type="incoming", turn_id="stall", respond_ts=t)
    _msg(db, type="outgoing", turn_id="stall", respond_ts=t + timedelta(seconds=90))


def _scenario_no_reply(db, now):
    _scenario_healthy(db, now)
    _msg(db, type="incoming", turn_id="silent", respond_ts=now - timedelta(minutes=12))


def _scenario_webhook_lag(db, now):
    """Reply was fast, but the inbound webhook arrived 40s late - visible as lag."""
    t = now - timedelta(minutes=3)
    _msg(db, type="incoming", turn_id="lag", respond_ts=t, ingest_at=t + timedelta(seconds=40))
    _msg(db, type="outgoing", turn_id="lag", respond_ts=t + timedelta(seconds=44))


def _scenario_proactive_only(db, now):
    """A campaign blast with no incoming - must not enter the SLA at all."""
    for i in range(20):
        _msg(db, type="outgoing", turn_id=None, respond_ts=now - timedelta(minutes=i + 1))


SCENARIOS = [
    ("healthy - 40 turns @ 3s", _scenario_healthy),
    ("degraded - 40 turns @ 25s", _scenario_degraded),
    ("one stalled turn among 40 healthy", _scenario_one_stalled),
    ("incoming with no reply (12m)", _scenario_no_reply),
    ("webhook arrived 40s late", _scenario_webhook_lag),
    ("proactive sends only (no turns)", _scenario_proactive_only),
]


def main() -> int:
    db = SessionLocal()
    try:
        settings = db.query(SystemSetting).first()
        target = int(getattr(settings, "chat_latency_p99_target_seconds", 10) or 10)
        mult = int(getattr(settings, "chat_latency_ceiling_multiplier", 3) or 3)
        min_sample = int(getattr(settings, "chat_latency_min_sample", 30) or 30)
        no_reply = int(getattr(settings, "chat_latency_no_reply_minutes", 5) or 5)

        print(f"p99 target {target}s | ceiling {target * mult}s | "
              f"min sample {min_sample} | no-reply {no_reply}m\n")

        now = datetime.utcnow()
        for label, build in SCENARIOS:
            _clear(db)
            build(db, now)
            db.flush()

            since = now - timedelta(hours=1)
            stats = latency.compute_latency_stats(db, since=since)
            is_bad, detail = _eval_chat_latency(db, now, settings)

            p99 = f"{stats.p99:.1f}s" if stats.p99 is not None else "-"
            print(f"{label:38} turns={stats.count:<3} p99={p99:<8} "
                  f"{'ALERT' if is_bad else 'ok'}")
            if detail:
                print(f"{'':38} {detail}")

            lags = [t.webhook_lag_seconds for t in latency.get_turns(db, since=since)
                    if t.webhook_lag_seconds is not None]
            if lags:
                print(f"{'':38} webhook lag: max {max(lags):.0f}s")
        return 0
    finally:
        db.rollback()  # nothing above is ever committed
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
