"""WhatsApp round-trip latency: turn pairing, percentiles, breach detection.

Covers UAC OBS-S4-05 .. OBS-S4-18 (CRM side; the n8n contract half is OBS-S4-01..04).

The measurement is deliberately narrow, and each narrowing is pinned by a test here:

- Both timestamps come from `respond_ts` (Respond's clock). Never `sent_at`, which n8n
  currently fills with its own `Date.now()` and which, on real data, can make an outgoing
  message appear to precede the incoming it answers.
- The clock stops at *sent*, not delivered. Delivery is the recipient's handset; an
  offline user must not be able to breach our p99.
- Pairing is by `turn_id` only. Rows without one are proactive sends and leave the
  denominator entirely rather than being paired by proximity.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy import BigInteger, Integer
from sqlalchemy.types import JSON

from app.database import Base
from app.models.chat_history import ChatHistory
from app.services import chat_latency_service as svc
from tests._pg_fixture import blank_session



_MODELS = [ChatHistory]


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


NOW = datetime(2026, 7, 20, 12, 0, 0)


def _msg(
    db,
    *,
    type,
    turn_id=None,
    respond_ts=None,
    sent_at=None,
    contact_id="900000063",
    delivery_status=None,
    message_id=None,
    ingest_at=None,
    commit=True,
):
    row = ChatHistory(
        channel="whatsapp",
        contact_id=contact_id,
        phone_number="+60160000517",
        message="hello",
        sent_at=sent_at or respond_ts or NOW,
        type=type,
        turn_id=turn_id,
        respond_ts=respond_ts,
        delivery_status=delivery_status,
        message_id=message_id or str(uuid.uuid4().int)[:16],
        ingest_at=ingest_at,
    )
    db.add(row)
    if commit:
        db.commit()
    return row


def _turn(db, turn_id, *, in_at, latency_s, **kw):
    """One complete turn: incoming at `in_at`, reply `latency_s` later."""
    _msg(db, type="incoming", turn_id=turn_id, respond_ts=in_at, **kw)
    _msg(db, type="outgoing", turn_id=turn_id, respond_ts=in_at + timedelta(seconds=latency_s), **kw)


# --------------------------------------------------------------------------- #
# Pairing                                                                     #
# --------------------------------------------------------------------------- #
def test_pairs_incoming_to_outgoing_by_turn_id(db):
    _turn(db, "48213", in_at=NOW, latency_s=4.2)
    turns = svc.get_turns(db, since=NOW - timedelta(hours=1))
    assert len(turns) == 1
    assert turns[0].turn_id == "48213"
    assert turns[0].latency_seconds == pytest.approx(4.2)


def test_pairing_is_immune_to_bursts(db):
    """3 rapid incomings, 3 replies - proximity pairing would mis-assign these."""
    _turn(db, "a", in_at=NOW, latency_s=30)          # slow
    _turn(db, "b", in_at=NOW + timedelta(seconds=1), latency_s=2)
    _turn(db, "c", in_at=NOW + timedelta(seconds=2), latency_s=3)
    by_turn = {t.turn_id: t.latency_seconds for t in svc.get_turns(db, since=NOW - timedelta(hours=1))}
    assert by_turn == pytest.approx({"a": 30.0, "b": 2.0, "c": 3.0})


def test_proactive_send_is_excluded_not_paired(db):
    """An outgoing with no turn_id must not steal a pairing or invent a turn."""
    _msg(db, type="incoming", turn_id="48213", respond_ts=NOW)
    _msg(db, type="outgoing", turn_id=None, respond_ts=NOW + timedelta(seconds=1))  # campaign
    _msg(db, type="outgoing", turn_id="48213", respond_ts=NOW + timedelta(seconds=6))
    turns = svc.get_turns(db, since=NOW - timedelta(hours=1))
    assert len(turns) == 1
    assert turns[0].latency_seconds == pytest.approx(6.0)


def test_multi_part_reply_measures_the_first_outgoing(db):
    """Perceived responsiveness is when the first reply lands, not the last."""
    _msg(db, type="incoming", turn_id="t", respond_ts=NOW)
    _msg(db, type="outgoing", turn_id="t", respond_ts=NOW + timedelta(seconds=3))
    _msg(db, type="outgoing", turn_id="t", respond_ts=NOW + timedelta(seconds=9))
    turns = svc.get_turns(db, since=NOW - timedelta(hours=1))
    assert turns[0].latency_seconds == pytest.approx(3.0)


def test_unresolved_respond_ts_is_not_measured(db):
    """A row awaiting resolution has no trustworthy clock yet - omit, don't guess."""
    _msg(db, type="incoming", turn_id="t", respond_ts=None, sent_at=NOW)
    _msg(db, type="outgoing", turn_id="t", respond_ts=None, sent_at=NOW + timedelta(seconds=5))
    assert svc.get_turns(db, since=NOW - timedelta(hours=1)) == []


def test_sent_at_is_never_used_as_the_clock(db):
    """Real data has outgoing sent_at preceding its own incoming. Must not leak in."""
    _msg(db, type="incoming", turn_id="t", respond_ts=NOW, sent_at=NOW + timedelta(seconds=10))
    _msg(
        db, type="outgoing", turn_id="t",
        respond_ts=NOW + timedelta(seconds=4),
        sent_at=NOW - timedelta(seconds=5),  # inverted, as observed in production
    )
    turns = svc.get_turns(db, since=NOW - timedelta(hours=1))
    assert turns[0].latency_seconds == pytest.approx(4.0)  # not -15, not 10


def test_incoming_with_no_reply_is_reported_separately(db):
    _msg(db, type="incoming", turn_id="lonely", respond_ts=NOW)
    assert svc.get_turns(db, since=NOW - timedelta(hours=1)) == []
    unanswered = svc.get_unanswered_turns(db, now=NOW + timedelta(minutes=6), older_than_seconds=300)
    assert [u.turn_id for u in unanswered] == ["lonely"]


def test_unanswered_within_threshold_is_not_flagged(db):
    _msg(db, type="incoming", turn_id="recent", respond_ts=NOW)
    unanswered = svc.get_unanswered_turns(db, now=NOW + timedelta(minutes=2), older_than_seconds=300)
    assert unanswered == []


# --------------------------------------------------------------------------- #
# Percentiles                                                                 #
# --------------------------------------------------------------------------- #
def test_p99_tolerates_exactly_one_percent(db):
    """99 fast + 1 slow is *compliant*: p99 by definition allows 1% to exceed.

    Pinned because it is tempting to read p99 as "the worst case" - the single slow
    turn shows up in `max`, not in p99, and an alert wired to p99 must not fire here.
    """
    for i in range(100):
        _turn(db, f"t{i}", in_at=NOW + timedelta(minutes=i), latency_s=1 if i < 99 else 45)
    stats = svc.compute_latency_stats(db, since=NOW - timedelta(hours=3))
    assert stats.count == 100
    assert stats.p50 == pytest.approx(1.0)
    assert stats.p99 == pytest.approx(1.0)
    assert stats.max == pytest.approx(45.0)


def test_p99_reflects_a_slow_tail_beyond_one_percent(db):
    """5 slow out of 100 pushes the 99th-rank value into the tail."""
    for i in range(100):
        _turn(db, f"t{i}", in_at=NOW + timedelta(minutes=i), latency_s=1 if i < 95 else 45)
    stats = svc.compute_latency_stats(db, since=NOW - timedelta(hours=3))
    assert stats.p99 == pytest.approx(45.0)


def test_stats_empty_window_is_not_a_breach(db):
    stats = svc.compute_latency_stats(db, since=NOW - timedelta(hours=1))
    assert stats.count == 0
    assert stats.p99 is None
    assert svc.evaluate_breach(stats, target_seconds=10).breached is False


def test_breach_when_p99_exceeds_target(db):
    for i in range(40):  # above the default min_sample of 30
        _turn(db, f"t{i}", in_at=NOW + timedelta(minutes=i), latency_s=30)
    stats = svc.compute_latency_stats(db, since=NOW - timedelta(hours=2))
    assert svc.evaluate_breach(stats, target_seconds=10).breached is True


def test_no_breach_when_p99_within_target(db):
    for i in range(40):
        _turn(db, f"t{i}", in_at=NOW + timedelta(minutes=i), latency_s=2)
    stats = svc.compute_latency_stats(db, since=NOW - timedelta(hours=2))
    assert svc.evaluate_breach(stats, target_seconds=10).breached is False


def test_min_sample_size_prevents_alerting_on_one_turn(db):
    """One slow turn must not move a fleet-level p99 alert on its own."""
    _turn(db, "only", in_at=NOW, latency_s=60)
    stats = svc.compute_latency_stats(db, since=NOW - timedelta(hours=1))
    assert svc.evaluate_breach(stats, target_seconds=10, min_sample=30).breached is False


# --------------------------------------------------------------------------- #
# Hard ceiling - a single catastrophic turn                                   #
# --------------------------------------------------------------------------- #
def test_stalled_turn_detected_regardless_of_sample_size(db):
    """The webhook-failure case: one turn blows past 3x target, alert immediately."""
    _turn(db, "stalled", in_at=NOW, latency_s=35)
    stalled = svc.get_stalled_turns(db, since=NOW - timedelta(hours=1), ceiling_seconds=30)
    assert [s.turn_id for s in stalled] == ["stalled"]


def test_turn_under_ceiling_is_not_stalled(db):
    _turn(db, "ok", in_at=NOW, latency_s=12)
    assert svc.get_stalled_turns(db, since=NOW - timedelta(hours=1), ceiling_seconds=30) == []


# --------------------------------------------------------------------------- #
# Delivery is tracked but never SLA'd                                         #
# --------------------------------------------------------------------------- #
def test_undelivered_message_does_not_affect_latency(db):
    """Offline recipient: the reply was sent fast; delivery never happened."""
    _msg(db, type="incoming", turn_id="t", respond_ts=NOW)
    _msg(
        db, type="outgoing", turn_id="t",
        respond_ts=NOW + timedelta(seconds=2),
        delivery_status="sent",
    )
    stats = svc.compute_latency_stats(db, since=NOW - timedelta(hours=1))
    assert stats.p99 == pytest.approx(2.0)
    assert svc.evaluate_breach(stats, target_seconds=10, min_sample=1).breached is False


def test_undelivered_counted_separately(db):
    _msg(
        db, type="outgoing", turn_id="t",
        respond_ts=NOW - timedelta(minutes=30),
        delivery_status="sent",
    )
    _msg(
        db, type="outgoing", turn_id="u",
        respond_ts=NOW - timedelta(minutes=30),
        delivery_status="delivered",
    )
    count = svc.count_undelivered(db, now=NOW, older_than_seconds=900)
    assert count == 1


def test_recently_sent_not_yet_delivered_is_not_counted(db):
    _msg(
        db, type="outgoing", turn_id="t",
        respond_ts=NOW - timedelta(seconds=30),
        delivery_status="sent",
    )
    assert svc.count_undelivered(db, now=NOW, older_than_seconds=900) == 0


# --------------------------------------------------------------------------- #
# Webhook lag - the failure mode this whole slice exists to catch              #
# --------------------------------------------------------------------------- #
def test_webhook_lag_is_measurable_and_separate_from_latency(db):
    _msg(
        db, type="incoming", turn_id="t",
        respond_ts=NOW,
        ingest_at=NOW + timedelta(seconds=45),  # webhook arrived 45s late
    )
    _msg(db, type="outgoing", turn_id="t", respond_ts=NOW + timedelta(seconds=50))
    turns = svc.get_turns(db, since=NOW - timedelta(hours=1))
    assert turns[0].latency_seconds == pytest.approx(50.0)
    assert turns[0].webhook_lag_seconds == pytest.approx(45.0)


def test_webhook_lag_null_when_ingest_at_missing(db):
    _turn(db, "t", in_at=NOW, latency_s=3)
    assert svc.get_turns(db, since=NOW - timedelta(hours=1))[0].webhook_lag_seconds is None


# --------------------------------------------------------------------------- #
# Watchdog entry point - fire / suppress / recover                            #
#                                                                             #
# Added after `run_chat_latency_watchdog` was found calling a `_mark_ok` helper #
# that did not exist: the recovery branch would have raised NameError in        #
# production, and no test touched it.                                          #
# --------------------------------------------------------------------------- #
@pytest.fixture
def wdb():
    """The watchdog also reads alert-state, settings and user tables.

    Those were listed explicitly so a sqlite engine could create just them; the
    blank schema carries every table, so the list is no longer needed.
    """
    with blank_session() as session:
        yield session


def test_watchdog_fires_then_recovers(wdb, monkeypatch):
    from app.services import system_health_alert_service as alert_svc
    from app.models.health_alert_state import HealthAlertState

    sent = []
    monkeypatch.setattr(
        alert_svc, "_notify_admins",
        lambda db, settings, *, subject, lines, is_alert, task=None, dedup_id=None:
            sent.append(subject),
    )
    monkeypatch.setattr(alert_svc, "_utcnow_naive", lambda: NOW)

    # Breach: 40 turns well over a 10s target.
    for i in range(40):
        _turn(wdb, f"b{i}", in_at=NOW - timedelta(minutes=i + 1), latency_s=45)

    out = alert_svc.run_chat_latency_watchdog(wdb)
    assert out["bad"] is True and out["fired"] is True
    assert any("chat_latency" in s for s in sent)

    # Clear the breach; the recovery branch must not explode and must notify once.
    wdb.query(ChatHistory).delete()
    wdb.commit()
    sent.clear()

    out = alert_svc.run_chat_latency_watchdog(wdb)
    assert out["bad"] is False and out["recovered"] is True
    assert any("Recovered" in s for s in sent)
    row = wdb.query(HealthAlertState).filter(
        HealthAlertState.alert_key == "chat_latency"
    ).one()
    assert row.state == "ok"
    assert row.last_detail is None


def test_watchdog_quiet_when_healthy(wdb, monkeypatch):
    from app.services import system_health_alert_service as alert_svc

    sent = []
    monkeypatch.setattr(
        alert_svc, "_notify_admins",
        lambda db, settings, *, subject, lines, is_alert, task=None, dedup_id=None:
            sent.append(subject),
    )
    monkeypatch.setattr(alert_svc, "_utcnow_naive", lambda: NOW)

    for i in range(40):
        _turn(wdb, f"h{i}", in_at=NOW - timedelta(minutes=i + 1), latency_s=2)

    out = alert_svc.run_chat_latency_watchdog(wdb)
    assert out["bad"] is False and out["fired"] is False and out["recovered"] is False
    assert sent == []


# --------------------------------------------------------------------------- #
# Configurable alerting percentile (OBS-S4-20)                                #
# --------------------------------------------------------------------------- #
class TestConfigurablePercentile:
    """p50/p95/p99 were all computed but only p99 could alert, hardcoded.

    Which percentile you hold yourself to is a policy decision - a chattier
    channel may want p95 - so it belongs in settings, not in the code.
    """

    def _stats(self):
        # 100 turns: 95 fast, 4 at 20s, 1 at 60s.
        # p95 = 5.0 (the 95th value), p99 = 20.0, max = 60.0.
        values = [5.0] * 95 + [20.0] * 4 + [60.0]
        return svc.compute_latency_stats_from_values(values)

    def test_p99_is_the_default(self):
        stats = self._stats()
        v = svc.evaluate_breach(stats, target_seconds=10, min_sample=30)
        assert v.breached is True          # p99 = 20 > 10
        assert "p99" in v.reason

    def test_p95_can_be_selected(self):
        stats = self._stats()
        v = svc.evaluate_breach(stats, target_seconds=10, min_sample=30, percentile=95)
        # p95 = 5s, comfortably inside a 10s target - the same data that breaches
        # at p99 passes at p95. That is the whole point of making it selectable.
        assert v.breached is False

    def test_p95_still_breaches_when_it_should(self):
        stats = svc.compute_latency_stats_from_values([50.0] * 100)
        v = svc.evaluate_breach(stats, target_seconds=10, min_sample=30, percentile=95)
        assert v.breached is True
        assert "p95" in v.reason

    def test_p50_is_selectable(self):
        stats = self._stats()
        v = svc.evaluate_breach(stats, target_seconds=1, min_sample=30, percentile=50)
        assert v.breached is True
        assert "p50" in v.reason

    def test_unknown_percentile_falls_back_to_p99(self):
        """A bad settings value must not silently disable alerting."""
        stats = self._stats()
        v = svc.evaluate_breach(stats, target_seconds=10, min_sample=30, percentile=42)
        assert v.breached is True
        assert "p99" in v.reason

    def test_min_sample_still_applies_to_any_percentile(self):
        stats = svc.compute_latency_stats_from_values([50.0] * 5)
        assert svc.evaluate_breach(stats, target_seconds=10, min_sample=30, percentile=95).breached is False
