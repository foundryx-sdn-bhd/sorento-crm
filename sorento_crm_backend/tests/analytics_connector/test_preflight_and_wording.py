"""CLAUDE-CONNECTOR r3 follow-up: the preflight sits outside the api-call log, and the manifest wording.

* ConnectorPreflightMiddleware is OUTERMOST of ApiCallLogMiddleware and IdempotencyMiddleware, so a
  Connector request carrying `X-Source` (which makes the api-call log buffer and record the call) is
  token-checked first: a refused one is 401 and leaves no `api_call_logs` row.
* No manifest description still names the dropped chatbot params or signed URLs, or carries the
  sentence fragments their removal left behind.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.analytics_connector._helpers import BASE, assert_error, pin_settings


def test_a_refused_connector_call_with_x_source_writes_no_api_call_log(monkeypatch):
    import app.services.api_call_log_service as svc
    import app.services.runtime_settings  # noqa: F401  (see below)
    from app.main import app

    # The stub session below is for the api-call log only. `verify_token` imports
    # runtime_settings lazily, and that module binds `SessionLocal` by name at import, so a
    # worker that had not imported it yet bound the stub instead: the settings read failed,
    # the connector secret failed closed and the call was 503 "not configured", not 401
    # (release run 38287204810). Importing it first keeps its real session either way.

    written: list[dict] = []
    monkeypatch.setattr(svc, "write_call_log", lambda db, **fields: written.append(fields))

    class _Session:
        def close(self):
            pass

    monkeypatch.setattr("app.database.SessionLocal", lambda: _Session())
    pin_settings(monkeypatch)
    resp = TestClient(app).post(
        f"{BASE}/query",
        content=b"x" * (2 * 1024 * 1024),
        headers={"Authorization": "Connector garbage", "X-Source": "zzt-mcp", "Content-Type": "application/json"},
    )
    assert_error(resp, 401, "invalid_signature")
    assert written == []


def test_the_preflight_wraps_the_api_call_log_and_idempotency():
    from app.main import app

    order = [m.cls.__name__ for m in app.user_middleware]  # outermost first
    pre = order.index("ConnectorPreflightMiddleware")
    assert pre < order.index("ApiCallLogMiddleware"), order
    assert pre < order.index("IdempotencyMiddleware"), order


@pytest.mark.parametrize(
    "fragment", ["signed URL", "contact_id", "space_id", "always pass both", "Holding the agent"]
)
def test_no_manifest_description_keeps_a_dropped_param_fragment(fragment):
    from app.services.analytics_connector.manifest import build_manifest

    for t in build_manifest()["tools"]:
        assert fragment.lower() not in t["description"].lower(), (t["name"], fragment)
