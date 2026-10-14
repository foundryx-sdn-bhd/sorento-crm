"""HA-MONITOR: the scheduler heartbeat to Healthchecks.io.

The worker's scheduler pings `HC_SCHEDULER_URL` from a real scheduled job, so a dead
scheduler (or one that can no longer read the DB) shows up on an outside watcher. The
ping must never be able to hurt the worker: unset means nothing happens, it never
raises, it has a short timeout and it does not block the scheduler thread.

No DB needed: the tick's session is replaced, and the HTTP call is mocked.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager

import httpx
import pytest

from app.scheduler import task_scheduler
from app.services import healthchecks_ping

URL = "https://hc-ping.example.test/abc-123"


class _Recorder:
    def __init__(self, exc=None, block: threading.Event | None = None):
        self.calls: list[tuple[str, float]] = []
        self.exc = exc
        self.block = block

    def __call__(self, url, *args, timeout=None, **kwargs):
        self.calls.append((url, timeout))
        if self.block is not None:
            self.block.wait(5)
        if self.exc is not None:
            raise self.exc
        return httpx.Response(200, text="OK")


def _ping_and_wait(url, **kwargs):
    thread = healthchecks_ping.ping(url, **kwargs)
    if thread is not None:
        thread.join(5)
    return thread


# --- the ping helper -------------------------------------------------------------------


@pytest.mark.parametrize("url", [None, "", "   "])
def test_unset_url_does_nothing(monkeypatch, url):
    rec = _Recorder()
    monkeypatch.setattr(healthchecks_ping.httpx, "get", rec)
    assert healthchecks_ping.ping(url) is None
    assert rec.calls == []


def test_ping_hits_the_url_with_a_short_timeout(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(healthchecks_ping.httpx, "get", rec)
    _ping_and_wait(URL)
    assert len(rec.calls) == 1
    called_url, timeout = rec.calls[0]
    assert called_url == URL
    assert timeout is not None and 0 < timeout <= 10


def test_fail_ping_hits_the_fail_endpoint(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(healthchecks_ping.httpx, "get", rec)
    _ping_and_wait(URL + "/", fail=True)
    assert rec.calls[0][0] == URL + "/fail"


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("down"),
        httpx.ReadTimeout("slow"),
        RuntimeError("anything else"),
    ],
)
def test_send_never_raises_and_never_logs_the_url(monkeypatch, caplog, exc):
    """The ping URL is a credential of sorts (anyone with it can fake a heartbeat)."""
    monkeypatch.setattr(healthchecks_ping.httpx, "get", _Recorder(exc=exc))
    with caplog.at_level(logging.DEBUG):
        assert healthchecks_ping._send(URL) is False
    assert "abc-123" not in caplog.text


def test_send_reports_a_non_2xx_answer_as_failed(monkeypatch):
    monkeypatch.setattr(
        healthchecks_ping.httpx, "get", lambda url, **kw: httpx.Response(404, text="not found")
    )
    assert healthchecks_ping._send(URL) is False


def test_ping_is_fire_and_forget(monkeypatch):
    """A hanging Healthchecks must not hold the scheduler's job thread."""
    gate = threading.Event()
    rec = _Recorder(block=gate)
    monkeypatch.setattr(healthchecks_ping.httpx, "get", rec)
    started = time.monotonic()
    thread = healthchecks_ping.ping(URL)
    elapsed = time.monotonic() - started
    try:
        assert elapsed < 0.5
        assert thread is not None and thread.daemon
    finally:
        gate.set()
        thread.join(5)


def test_ping_survives_a_thread_that_cannot_start(monkeypatch):
    class _Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(healthchecks_ping.threading, "Thread", _Boom)
    assert healthchecks_ping.ping(URL) is None


# --- the scheduled tick ----------------------------------------------------------------


class _FakeDb:
    def __init__(self, exc=None):
        self.exc = exc
        self.executed = 0

    def execute(self, *a, **kw):
        self.executed += 1
        if self.exc is not None:
            raise self.exc


def _patch_tick(monkeypatch, db: _FakeDb | None = None, session_exc=None):
    opened: list[str] = []
    pings: list[tuple[str, bool]] = []

    @contextmanager
    def fake_session(name="scheduler"):
        opened.append(name)
        if session_exc is not None:
            raise session_exc
        yield db or _FakeDb()

    monkeypatch.setattr(task_scheduler, "scheduler_session", fake_session)
    monkeypatch.setattr(
        task_scheduler.healthchecks_ping,
        "ping",
        lambda url, fail=False: pings.append((url, fail)),
    )
    return opened, pings


def test_tick_does_nothing_when_env_unset(monkeypatch):
    monkeypatch.delenv("HC_SCHEDULER_URL", raising=False)
    opened, pings = _patch_tick(monkeypatch)
    task_scheduler._healthchecks_scheduler_tick()
    assert opened == []
    assert pings == []


def test_tick_reads_the_db_then_pings_success(monkeypatch):
    monkeypatch.setenv("HC_SCHEDULER_URL", URL)
    db = _FakeDb()
    opened, pings = _patch_tick(monkeypatch, db=db)
    task_scheduler._healthchecks_scheduler_tick()
    assert db.executed == 1
    assert pings == [(URL, False)]


@pytest.mark.parametrize("where", ["execute", "session"])
def test_tick_pings_fail_when_the_db_read_breaks_and_never_raises(monkeypatch, where):
    monkeypatch.setenv("HC_SCHEDULER_URL", URL)
    err = RuntimeError("db gone")
    if where == "execute":
        opened, pings = _patch_tick(monkeypatch, db=_FakeDb(exc=err))
    else:
        opened, pings = _patch_tick(monkeypatch, session_exc=err)
    task_scheduler._healthchecks_scheduler_tick()
    assert pings == [(URL, True)]


def test_tick_never_raises_when_the_ping_itself_breaks(monkeypatch):
    monkeypatch.setenv("HC_SCHEDULER_URL", URL)
    _patch_tick(monkeypatch)

    def boom(url, fail=False):
        raise RuntimeError("ping exploded")

    monkeypatch.setattr(task_scheduler.healthchecks_ping, "ping", boom)
    task_scheduler._healthchecks_scheduler_tick()


def test_start_scheduler_registers_the_heartbeat_job(monkeypatch):
    """The ping comes from a real APScheduler job, every few minutes."""
    jobs: dict[str, dict] = {}

    class _FakeScheduler:
        def add_job(self, func, trigger=None, id=None, **kw):
            jobs[id] = {"func": func, "trigger": trigger}

        def start(self):
            pass

    monkeypatch.setattr(task_scheduler, "BackgroundScheduler", _FakeScheduler)
    monkeypatch.setattr(task_scheduler, "register_task_handlers", lambda: None)
    _patch_tick(monkeypatch, session_exc=RuntimeError("no db in this test"))

    task_scheduler.start_scheduler()

    job = jobs["healthchecks_scheduler_ping"]
    assert job["func"] is task_scheduler._healthchecks_scheduler_tick
    assert isinstance(job["trigger"], task_scheduler.IntervalTrigger)
    interval = job["trigger"].interval.total_seconds()
    assert interval == task_scheduler.HC_SCHEDULER_PING_MINUTES * 60
    assert 60 <= interval <= 15 * 60
