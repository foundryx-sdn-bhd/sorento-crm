"""HA-MONITOR: GET /api/v1/health, the outside monitor's probe of the backend.

On prod, `https://<CRM_DOMAIN>/health` is answered by nginx itself (200 text/plain
"healthy", crew probe 15 Oct), so a monitor on it stays green with the backend dead.
`/api/v1/*` is what nginx already sends to the backend, so the probe lives there.

It is unauthenticated and polled every minute from outside, so it must be cheap (one
`SELECT 1`) and say nothing beyond status + checks: no git sha, no prompt version, no
error text (a DB error can carry the host and user).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import app.database as database
from app.main import app


def test_healthy_without_any_credentials():
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "checks": {"database": "ok"}}
    # The Uptime Kuma keyword in documentation/runbooks/monitoring.md.
    assert '"status":"healthy"' in response.text


def test_db_down_is_503_and_leaks_nothing(monkeypatch):
    class _BrokenSession:
        def execute(self, *a, **kw):
            raise RuntimeError(
                'connection to server at "db-secret-host" failed: password authentication failed for user "crm_admin"'
            )

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: _BrokenSession())
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "checks": {"database": "error"}}
    assert "db-secret-host" not in response.text
    assert "crm_admin" not in response.text
    # "unhealthy" must not satisfy the monitor's keyword.
    assert '"status":"healthy"' not in response.text


def test_session_that_cannot_open_is_503(monkeypatch):
    def _no_session():
        raise RuntimeError("pool exhausted at db-secret-host")

    monkeypatch.setattr(database, "SessionLocal", _no_session)
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 503
    assert "db-secret-host" not in response.text


def test_a_bogus_bearer_token_does_not_change_the_answer():
    """Kuma sends no credentials; a stray header must not turn the probe into a 401."""
    response = TestClient(app).get("/api/v1/health", headers={"Authorization": "Bearer not-a-session"})
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
