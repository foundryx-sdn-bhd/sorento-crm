"""CLAUDE-CONNECTOR r3: the OAuth server, grants admin and connector session moved to SS-CONNECTOR.

Contract section 0: "Removed from #1547 once SS covers them: sorento OAuth routes/tables, grants admin
page, the connector `user_sessions` access token, the internal key, `sorento_analytics_mcp/`."

Not pinned (left alone on purpose): the `user_sessions.auth_method` CHECK still lists
`claude_connector`; dropping it is not required by r3.
"""
from __future__ import annotations

import importlib

import pytest

from tests._pg_fixture import blank_session
from tests.analytics_connector._helpers import OLD_BASE, client_for, make_user, staff_headers


@pytest.fixture()
def env(monkeypatch):
    with blank_session() as db:
        with client_for(db, monkeypatch) as client:
            yield db, client


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", f"{OLD_BASE}/oauth/register"),
        ("POST", f"{OLD_BASE}/oauth/authorize"),
        ("POST", f"{OLD_BASE}/oauth/token"),
        ("GET", f"{OLD_BASE}/authorize/zzt-req"),
        ("POST", f"{OLD_BASE}/authorize/zzt-req/approve"),
        ("POST", f"{OLD_BASE}/authorize/zzt-req/deny"),
        ("GET", f"{OLD_BASE}/me"),
        ("POST", f"{OLD_BASE}/audit"),
        ("GET", f"{OLD_BASE}/grants"),
        ("GET", f"{OLD_BASE}/grants/zzt-grant"),
        ("POST", f"{OLD_BASE}/grants/zzt-grant/revoke"),
        ("GET", f"{OLD_BASE}/datasets"),
    ],
)
def test_the_old_analytics_connector_routes_are_gone(env, method, path):
    db, client = env
    user = make_user(db, "old", role_slug="admin")
    resp = client.request(method, path, headers=staff_headers(db, user), json={})
    assert resp.status_code == 404, (method, path, resp.status_code, resp.text)


def test_the_oauth_service_module_is_gone():
    with pytest.raises(ImportError):
        importlib.import_module("app.services.analytics_connector.oauth")


def test_the_internal_key_setting_is_gone():
    from app.config import Settings

    assert "analytics_connector_internal_key" not in Settings.model_fields
    assert "analytics_connector_public_base_url" not in Settings.model_fields
