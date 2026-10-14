"""HA-MEM-DEPLOY-GUARD: /health reports Redis and scheduler state; /health/deep fails on them.

`/health` is blue/green's readiness gate (the backend image's HEALTHCHECK curls it, and
`scripts/blue_green_deploy.sh` waits for "healthy"). Its STATUS CODE must keep depending on
the database only: a Redis blip during a deploy must not stop the new colour from going live,
and a slow Redis must not make the probe itself slow. Monitors poll `/health/deep`, which
returns 503 when the database, Redis or the scheduler is not OK.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.scheduled_task import ScheduledTask
from app.services import health_probes
from tests._pg_fixture import pg_session, unique_code

DEAD_REDIS = "redis://127.0.0.1:1/0"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def scheduler_ok(monkeypatch):
    monkeypatch.setattr(
        health_probes,
        "scheduler_state",
        lambda db, now=None: {"status": "ok", "overdue_count": 0, "last_task_run_at": "2026-10-14T00:00:00"},
    )


@pytest.fixture
def scheduler_overdue(monkeypatch):
    monkeypatch.setattr(
        health_probes,
        "scheduler_state",
        lambda db, now=None: {"status": "overdue", "overdue_count": 3, "last_task_run_at": "2026-10-13T00:00:00"},
    )


# --- /health: richer body, same readiness ----------------------------------------


def test_health_body_names_redis_and_scheduler(client, scheduler_ok):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["redis"] == "ok"
    assert body["scheduler"]["status"] == "ok"
    assert "git_sha" in body and "prompt_version" in body  # existing fields kept


def test_health_stays_200_when_redis_is_down_and_the_scheduler_is_overdue(client, monkeypatch, scheduler_overdue):
    monkeypatch.setattr(health_probes.settings, "redis_url", DEAD_REDIS)
    r = client.get("/health")
    assert r.status_code == 200, "blue/green readiness must not depend on Redis or the scheduler"
    body = r.json()
    assert body["status"] == "healthy"
    assert body["redis"] == "down"
    assert body["scheduler"]["status"] == "overdue"


def test_health_is_not_slowed_by_a_hanging_probe(client, monkeypatch, scheduler_ok):
    def hang():
        time.sleep(6)
        return "ok"

    monkeypatch.setattr(health_probes, "redis_state", hang)
    started = time.monotonic()
    r = client.get("/health")
    elapsed = time.monotonic() - started
    assert r.status_code == 200
    assert r.json()["redis"] == "timeout"
    assert elapsed < 4, f"/health took {elapsed:.1f}s; the Dockerfile HEALTHCHECK allows 10s"


# --- /health/deep: for monitors ---------------------------------------------------


def test_deep_is_200_when_everything_is_ok(client, scheduler_ok):
    r = client.get("/health/deep")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "status": "healthy",
        "database": "ok",
        "redis": "ok",
        "scheduler": {"status": "ok", "overdue_count": 0, "last_task_run_at": "2026-10-14T00:00:00"},
    }


def test_deep_is_503_when_redis_is_down_and_leaks_no_connection_detail(client, monkeypatch, scheduler_ok):
    monkeypatch.setattr(health_probes.settings, "redis_url", DEAD_REDIS)
    r = client.get("/health/deep")
    assert r.status_code == 503
    assert r.json()["redis"] == "down"
    assert r.json()["status"] == "unhealthy"
    assert "redis://" not in r.text and "127.0.0.1" not in r.text


def test_deep_is_503_when_the_scheduler_is_overdue(client, scheduler_overdue):
    r = client.get("/health/deep")
    assert r.status_code == 503
    assert r.json()["scheduler"]["status"] == "overdue"


def test_deep_is_503_when_the_database_is_down(client, monkeypatch, scheduler_ok):
    import app.database as database

    def broken():
        raise RuntimeError("could not connect to server at db.internal:5432")

    monkeypatch.setattr(database, "SessionLocal", broken)
    r = client.get("/health/deep")
    assert r.status_code == 503
    assert r.json()["database"] == "down"
    assert "db.internal" not in r.text


# --- scheduler_state against the real table ---------------------------------------


def _task(db, *, enabled=True, last_run_at=None, minutes=1):
    t = ScheduledTask(
        key=unique_code("hdeep"),
        name="health deep probe",
        enabled=enabled,
        interval_unit="minutes",
        interval_value=minutes,
        last_run_at=last_run_at,
    )
    db.add(t)
    db.flush()
    return t


def test_scheduler_state_is_ok_when_no_enabled_task_is_overdue():
    now = datetime(2026, 10, 14, 12, 0, 0)
    with pg_session() as db:
        db.query(ScheduledTask).update({ScheduledTask.enabled: False})
        _task(db, last_run_at=now - timedelta(seconds=30))
        _task(db, enabled=False, last_run_at=now - timedelta(days=3))  # disabled: ignored
        state = health_probes.scheduler_state(db, now=now)
    assert state == {"status": "ok", "overdue_count": 0, "last_task_run_at": "2026-10-14T11:59:30"}


def test_scheduler_state_is_overdue_when_the_heartbeat_stopped():
    now = datetime(2026, 10, 14, 12, 0, 0)
    with pg_session() as db:
        db.query(ScheduledTask).update({ScheduledTask.enabled: False})
        _task(db, last_run_at=now - timedelta(minutes=20))  # every minute, 20 min ago
        _task(db, last_run_at=now - timedelta(seconds=10))
        state = health_probes.scheduler_state(db, now=now)
    assert state["status"] == "overdue"
    assert state["overdue_count"] == 1
    assert state["last_task_run_at"] == "2026-10-14T11:59:50"
