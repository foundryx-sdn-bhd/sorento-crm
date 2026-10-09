"""Background reconcile + Respond.io backoff (lane CHAT-LOCAL-FIRST, UAC section D).

The `chat_history_reconcile` scheduled task selects contacts by recent activity
(AC-RC1), keeps at most `concurrency` calls in flight per workspace key (AC-RC2), honours
`Retry-After` and doubles without it (AC-RC3), and never raises out of the tick (AC-RC4).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.models.chat_history import ChatHistory
from app.models.chat_thread_sync_state import ChatThreadSyncState
from app.services import chat_thread_sync_service as sync
from app.services import conversation_event_bus as bus
from app.services import respond_rate_limit as rl
from tests._pg_fixture import blank_session

BASE = 1786000000000000


@pytest.fixture(autouse=True)
def _clean_backoff():
    rl.reset()
    yield
    rl.reset()


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


class _NullTransport:
    def publish(self, channel, payload):
        pass

    def subscribe(self, channel):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _quiet_bus():
    bus.set_transport(_NullTransport())
    yield
    bus.set_transport(None)


# ---------------------------------------------------------------------------
# respond_rate_limit
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, api_key="key-a"):
        self.api_key = api_key
        self.calls = 0


def _status_error(status: int, headers: dict | None = None) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        str(status),
        request=httpx.Request("GET", "https://api.respond.io/x"),
        response=httpx.Response(status, headers=headers or {}),
    )


def test_a_429_with_retry_after_blocks_the_key_for_that_long():
    client = _Client()

    def fn():
        client.calls += 1
        raise _status_error(429, {"Retry-After": "7"})

    with pytest.raises(rl.RateLimited) as exc:
        rl.call(client, fn)
    assert 6 <= exc.value.retry_after <= 7
    assert 6 <= rl.blocked_for(client) <= 7
    with pytest.raises(rl.RateLimited):
        rl.call(client, fn)
    assert client.calls == 1, "inside the window nothing is sent"


def test_a_429_with_an_http_date_retry_after_is_honoured():
    client = _Client()
    when = datetime.utcnow() + timedelta(seconds=20)
    header = when.strftime("%a, %d %b %Y %H:%M:%S GMT")
    rl.note_429(client, httpx.Response(429, headers={"Retry-After": header}))
    assert 18 <= rl.blocked_for(client) <= 20


def test_without_retry_after_the_wait_doubles_and_caps():
    client = _Client()
    waits = [rl.note_429(client, httpx.Response(429), now=1000.0 + i) for i in range(8)]
    assert waits == [1, 2, 4, 8, 16, 32, 60, 60]


def test_a_success_clears_the_backoff():
    client = _Client()
    rl.note_429(client, httpx.Response(429), now=1000.0)
    assert rl.blocked_for(client, now=1000.5) > 0
    # Past the window the next call goes out; its success forgets the streak.
    assert rl.blocked_for(client, now=1002.0) == 0
    assert rl.call(client, lambda: "ok") == "ok"
    assert rl.blocked_for(client) == 0
    assert rl.note_429(client, httpx.Response(429)) == 1, "the doubling restarts"


def test_a_404_is_the_contacts_problem_not_the_keys():
    client = _Client()

    def fn():
        raise _status_error(404)

    with pytest.raises(httpx.HTTPStatusError):
        rl.call(client, fn)
    assert rl.blocked_for(client) == 0


def test_a_5xx_or_a_timeout_backs_off_like_a_429_without_a_header():
    client = _Client()

    def boom():
        raise _status_error(503)

    with pytest.raises(httpx.HTTPStatusError):
        rl.call(client, boom)
    assert 0 < rl.blocked_for(client) <= 1

    other = _Client("key-b")

    def timeout():
        raise httpx.ReadTimeout("slow")

    with pytest.raises(httpx.ReadTimeout):
        rl.call(other, timeout)
    assert 0 < rl.blocked_for(other) <= 1


def test_keys_back_off_independently():
    a, b = _Client("key-a"), _Client("key-b")
    rl.note_429(a, httpx.Response(429, headers={"Retry-After": "30"}))
    assert rl.blocked_for(a) > 0
    assert rl.blocked_for(b) == 0


# ---------------------------------------------------------------------------
# run_reconcile
# ---------------------------------------------------------------------------


def _contact(i: int) -> str:
    return f"ZZT77{i:04d}"


def _seed_contact(db, i: int, *, activity: datetime, n_rows: int = 2):
    for k in range(n_rows):
        db.add(
            ChatHistory(
                channel="whatsapp",
                contact_id=_contact(i),
                phone_number=f"+6010000{i:04d}",
                first_name=f"Zzt{i}",
                message=f"body {k}",
                sent_at=activity - timedelta(seconds=n_rows - k),
                type="incoming",
                message_id=str(BASE + i * 100_000_000 + k * 1_000_000),
            )
        )
    db.add(
        ChatThreadSyncState(
            channel="whatsapp",
            contact_id=_contact(i),
            last_activity_at=activity,
            # The Respond-side watermark: the newest id a Respond read confirmed.
            newest_synced_message_id=str(BASE + i * 100_000_000 + (n_rows - 1) * 1_000_000),
        )
    )
    db.flush()


class _SameSession:
    """The reconcile opens one session per contact; under test that must be THIS
    transaction (the scratch schema is pinned per connection), so close() is a no-op."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def close(self):
        pass


class FakeClient:
    def __init__(self, api_key: str, *, newer: list[dict] | None = None, fail: Exception | None = None):
        self.api_key = api_key
        self.newer = newer or []
        self.fail = fail
        self.calls: list[dict] = []

    def list_messages(self, identifier, limit=50, cursor=None):
        self.calls.append({"identifier": identifier, "cursor": cursor})
        if self.fail is not None:
            raise self.fail
        return {"items": list(self.newer)}


def _task(**metadata):
    return SimpleNamespace(metadata_={"concurrency": 1, **metadata})


def test_only_contacts_with_recent_activity_are_selected(db):
    now = datetime(2026, 9, 30, 12, 0, 0)
    _seed_contact(db, 1, activity=now - timedelta(days=1))
    _seed_contact(db, 2, activity=now - timedelta(days=6))
    _seed_contact(db, 3, activity=now - timedelta(days=9))

    contacts = sync.active_contacts(db, since=now - timedelta(days=7), limit=100)

    assert [c.respond_io_id for c in contacts] == [_contact(1), _contact(2)]
    assert contacts[0].phone_number == "+60100000001"
    assert contacts[0].first_name == "Zzt1"


def test_the_tick_reads_one_delta_page_per_active_contact(db):
    """AC-RC1: one `cursorId=-<newest stored id>` read per contact, rows written."""
    now = datetime(2026, 9, 30, 12, 0, 0)
    _seed_contact(db, 1, activity=now - timedelta(hours=2))
    _seed_contact(db, 2, activity=now - timedelta(days=2))
    _seed_contact(db, 3, activity=now - timedelta(days=30))
    new_item = {
        "messageId": BASE + 900_000_000_000,
        "traffic": "incoming",
        "message": {"type": "text", "text": "new since last time"},
        "sender": {"source": "contact"},
        "status": [],
    }
    client = FakeClient("key-a", newer=[new_item])

    summary = sync.run_reconcile(
        db,
        _task(activity_days=7),
        client_for=lambda _db, _c: client,
        session_factory=lambda: _SameSession(db),
        now=now,
    )

    assert summary["selected"] == 2 and summary["synced"] == 2
    assert summary["written"] == 2
    assert sorted(c["identifier"] for c in client.calls) == [_contact(1), _contact(2)]
    assert all(str(c["cursor"]).startswith("-") for c in client.calls), "newer than the watermark"
    stored = db.query(ChatHistory).filter(ChatHistory.message_id == str(new_item["messageId"])).all()
    assert sorted(r.contact_id for r in stored) == [_contact(1), _contact(2)]


def test_a_429_pauses_the_key_and_the_rest_of_the_batch_is_skipped(db):
    """AC-RC3: after the 429 nothing else is sent on that key this tick."""
    now = datetime(2026, 9, 30, 12, 0, 0)
    for i in (1, 2, 3):
        _seed_contact(db, i, activity=now - timedelta(hours=i))
    client = FakeClient("key-a", fail=_status_error(429, {"Retry-After": "45"}))

    summary = sync.run_reconcile(
        db, _task(), client_for=lambda _db, _c: client,
        session_factory=lambda: _SameSession(db), now=now,
    )

    assert len(client.calls) == 1
    assert summary["rate_limited"] == 3
    assert summary["synced"] == 0
    assert 40 <= rl.blocked_for(client) <= 45


def test_the_cap_and_the_backoff_are_per_key(db):
    """AC-RC2: a throttled workspace does not stop another workspace's contacts."""
    now = datetime(2026, 9, 30, 12, 0, 0)
    for i in (1, 2, 3, 4):
        _seed_contact(db, i, activity=now - timedelta(hours=i))
    throttled = FakeClient("key-a", fail=_status_error(429, {"Retry-After": "45"}))
    healthy = FakeClient("key-b")
    clients = {_contact(1): throttled, _contact(2): healthy, _contact(3): throttled, _contact(4): healthy}

    summary = sync.run_reconcile(
        db, _task(), client_for=lambda _db, c: clients[c.respond_io_id],
        session_factory=lambda: _SameSession(db), now=now,
    )

    assert len(throttled.calls) == 1
    assert sorted(c["identifier"] for c in healthy.calls) == [_contact(2), _contact(4)]
    assert summary["synced"] == 2 and summary["rate_limited"] == 2


def test_a_failing_contact_is_recorded_and_the_tick_goes_on(db):
    """AC-RC4."""
    now = datetime(2026, 9, 30, 12, 0, 0)
    _seed_contact(db, 1, activity=now - timedelta(hours=1))
    _seed_contact(db, 2, activity=now - timedelta(hours=2))
    calls = {"n": 0}

    class Flaky(FakeClient):
        def list_messages(self, identifier, limit=50, cursor=None):
            calls["n"] += 1
            if identifier == _contact(1):
                raise _status_error(404)
            return {"items": []}

    client = Flaky("key-a")
    summary = sync.run_reconcile(
        db, _task(), client_for=lambda _db, _c: client,
        session_factory=lambda: _SameSession(db), now=now,
    )

    assert calls["n"] == 2
    assert summary["errors"] == 1 and summary["synced"] == 1
    state = (
        db.query(ChatThreadSyncState).filter(ChatThreadSyncState.contact_id == _contact(1)).one()
    )
    assert "404" in (state.last_error or "")
    assert rl.blocked_for(client) == 0, "a 404 is not a key problem"


def test_the_batch_limit_and_the_defaults_come_from_task_metadata(db):
    now = datetime(2026, 9, 30, 12, 0, 0)
    for i in (1, 2, 3):
        _seed_contact(db, i, activity=now - timedelta(hours=i))
    client = FakeClient("key-a")

    summary = sync.run_reconcile(
        db, _task(batch_limit=2), client_for=lambda _db, _c: client,
        session_factory=lambda: _SameSession(db), now=now,
    )

    assert summary["selected"] == 2, "newest activity first, capped"
    assert summary["activity_days"] == sync.DEFAULT_ACTIVITY_DAYS
    assert summary["concurrency"] == 1


def test_a_contact_whose_client_has_no_key_is_counted_not_called(db):
    now = datetime(2026, 9, 30, 12, 0, 0)
    _seed_contact(db, 1, activity=now - timedelta(hours=1))
    client = FakeClient("")

    summary = sync.run_reconcile(
        db, _task(), client_for=lambda _db, _c: client,
        session_factory=lambda: _SameSession(db), now=now,
    )

    assert client.calls == []
    assert summary["no_api_key"] == 1


def test_spacing_follows_how_long_the_contact_has_been_quiet():
    """Review S9: a 7-day window must not cost one call per contact per tick."""
    now = datetime(2026, 9, 30, 12, 0, 0)

    def state(activity_ago, synced_ago):
        return ChatThreadSyncState(
            channel="whatsapp",
            contact_id="x",
            last_activity_at=now - activity_ago,
            last_synced_at=None if synced_ago is None else now - synced_ago,
        )

    assert sync._sync_due(state(timedelta(days=6), None), now) is True, "never synced"
    assert sync._sync_due(state(timedelta(minutes=10), timedelta(minutes=5)), now) is True
    assert sync._sync_due(state(timedelta(hours=2), timedelta(minutes=5)), now) is False
    assert sync._sync_due(state(timedelta(hours=2), timedelta(minutes=31)), now) is True
    assert sync._sync_due(state(timedelta(days=6), timedelta(hours=35)), now) is False
    assert sync._sync_due(state(timedelta(days=6), timedelta(hours=37)), now) is True


def test_a_contact_synced_recently_for_how_quiet_it_is_waits(db):
    now = datetime(2026, 9, 30, 12, 0, 0)
    _seed_contact(db, 1, activity=now - timedelta(hours=2))
    _seed_contact(db, 2, activity=now - timedelta(minutes=10))
    for i, synced in ((1, timedelta(minutes=5)), (2, timedelta(minutes=5))):
        st = db.query(ChatThreadSyncState).filter(ChatThreadSyncState.contact_id == _contact(i)).one()
        st.last_synced_at = now - synced
    db.flush()

    contacts = sync.active_contacts(db, since=now - timedelta(days=7), limit=100, now=now)
    assert [c.respond_io_id for c in contacts] == [_contact(2)]


def test_at_most_concurrency_contacts_are_in_flight_per_key(db, monkeypatch):
    """AC-RC2, measured: the per-key pool never runs more than `concurrency` reads at
    once, whatever the batch size. No database in the measured path."""
    import threading
    import time

    now = datetime(2026, 9, 30, 12, 0, 0)
    for i in range(1, 7):
        _seed_contact(db, i, activity=now - timedelta(hours=i))
    lock = threading.Lock()
    in_flight = {"now": 0, "max": 0}

    def fake_reconcile_one(contact, client, session_factory):
        with lock:
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
        time.sleep(0.03)
        with lock:
            in_flight["now"] -= 1
        return {"fetched": 0, "written": 0, "error": None, "rate_limited": False}

    monkeypatch.setattr(sync, "_reconcile_one", fake_reconcile_one)
    client = FakeClient("key-a")

    for concurrency in (1, 2):
        in_flight["max"] = 0
        summary = sync.run_reconcile(
            db, _task(concurrency=concurrency), client_for=lambda _db, _c: client,
            session_factory=lambda: None, now=now,
        )
        assert summary["synced"] == 6
        assert in_flight["max"] == concurrency


def test_the_handler_enqueues_the_run_instead_of_blocking_the_heartbeat(monkeypatch):
    """Review B5: the heartbeat runs handlers one after another, so the tick is an RQ job."""
    from app.scheduler import task_scheduler
    from app.services import queue_service

    captured = {}

    def fake_enqueue(func, *args, queue_name=None, job_timeout=None, **kwargs):
        captured.update(func=func, args=args, queue_name=queue_name, job_timeout=job_timeout)
        return SimpleNamespace(id="job-1")

    monkeypatch.setattr(queue_service, "enqueue_job", fake_enqueue)
    task = SimpleNamespace(metadata_={"activity_days": 3, "concurrency": 1})

    out = task_scheduler._handler_chat_history_reconcile(None, task)

    from app.tasks.chat_reconcile_tasks import run_chat_history_reconcile

    assert out == {"enqueued": "job-1", "queue": "respond_io"}
    assert captured["func"] is run_chat_history_reconcile
    assert captured["args"] == ({"activity_days": 3, "concurrency": 1},)
    assert captured["queue_name"] == "respond_io"


def test_the_job_runs_once_at_a_time(monkeypatch):
    from app.services import queue_service
    from app.tasks import chat_reconcile_tasks as tasks

    class FakeRedis:
        def __init__(self):
            self.held = False

        def set(self, key, value, nx=False, ex=None):
            if self.held:
                return None
            self.held = True
            return True

        def delete(self, key):
            self.held = False

    fake = FakeRedis()
    monkeypatch.setattr(queue_service, "redis_conn", fake)
    fake.held = True
    assert tasks.run_chat_history_reconcile({}) == {"skipped": "in progress"}


def test_the_scheduler_registers_the_handler():
    from app.scheduler.task_scheduler import register_task_handlers
    from app.services.scheduled_task_service import TASK_HANDLERS

    register_task_handlers()
    assert "chat_history_reconcile" in TASK_HANDLERS
