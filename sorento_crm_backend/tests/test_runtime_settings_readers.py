"""SETTINGS-IN-DB-SORENTO AC-6: every reader honours a DB value saved AFTER import, no restart.

Env is None for every setting; the value lives only in the `system_settings` row, written through
`runtime_settings.update`. Readers resolve through `runtime_settings.get(name, db=None)`, which
opens its own `runtime_settings.SessionLocal()`; that is replaced here by a non-closing wrapper
around the blank-schema session.

Plan: documentation/plans/system/PLAN-settings-in-db-sorento.md, "Readers switched".
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import TEST_PREFIX, blank_session

WEBHOOK_URL = "/api/v1/public/respond/webhook"
DB_SECRET = "zzt-db-held-webhook-secret"

ALL_FIELDS = [
    "chat_topic_labeller_enabled",
    "connector_ss_to_product_secret",
    "connector_product_to_ss_secret",
    "connector_connection_id",
    "connector_ss_callback_url",
    "analytics_ro_database_url",
    "respond_webhook_secret",
]


def _pin_env(monkeypatch, **values):
    for name in ALL_FIELDS:
        value = values.get(name)
        if name in type(settings).model_fields:
            monkeypatch.setattr(settings, name, value, raising=False)
        else:
            monkeypatch.setitem(settings.__dict__, name, value)


class _KeepOpen:
    """Delegates to the blank-schema session; `close()` is a no-op so the test keeps its data."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, item):
        return getattr(self._inner, item)

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    from app.services import runtime_settings as rs

    with blank_session() as s:
        monkeypatch.setattr(rs, "SessionLocal", lambda: _KeepOpen(s))
        _pin_env(monkeypatch)  # env holds nothing for any setting
        yield s


@pytest.fixture
def save(db):
    """Write settings into the DB the way the screen does (after import, no restart)."""
    from app.models.user import User
    from app.services import runtime_settings as rs

    user = User(
        id=str(uuid.uuid4()),
        email=f"{TEST_PREFIX}-rsr-{uuid.uuid4().hex[:6]}@zzt.test".lower(),
        name=f"{TEST_PREFIX} RSR",
        avatar_storage_provider="s3",
    )
    db.add(user)
    db.commit()

    def _save(**payload):
        rs.update(db, payload, user.id)

    return _save


# --------------------------------------------------------------------------- Respond.io webhook


@pytest.fixture
def client(db):
    def _user():
        return {"id": "system"}

    def _db():
        yield db

    app.dependency_overrides[get_external_api_user] = _user
    app.dependency_overrides[get_current_user_or_api_key] = _user
    app.dependency_overrides[get_db] = _db
    with external_permissions_granted():
        yield TestClient(app)
    app.dependency_overrides.clear()


def _signed_post(client, secret: str):
    raw = json.dumps({"event_type": "ping", "event_id": "zzt-evt"}).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        WEBHOOK_URL, content=raw, headers={"x-respond-signature": sig, "content-type": "application/json"}
    )


def test_webhook_503_when_neither_env_nor_db_holds_a_secret(client):
    assert _signed_post(client, DB_SECRET).status_code == 503


def test_webhook_verifies_against_the_db_secret_without_a_restart(client, save):
    save(respond_webhook_secret=DB_SECRET)

    assert _signed_post(client, DB_SECRET).status_code != 503
    assert _signed_post(client, DB_SECRET).status_code != 401
    assert _signed_post(client, "someone-else").status_code == 401


def test_webhook_503_again_once_the_db_secret_is_cleared(client, save):
    save(respond_webhook_secret=DB_SECRET)
    assert _signed_post(client, DB_SECRET).status_code != 503

    save(respond_webhook_secret="")

    assert _signed_post(client, DB_SECRET).status_code == 503


# --------------------------------------------------------------------------- nightly labeller tick


@pytest.mark.parametrize(
    "db_value, env_value, expect_enqueued",
    [(True, False, 1), (False, True, 0)],
)
def test_labeller_tick_follows_the_db_bool_over_env(
    db, save, monkeypatch, db_value, env_value, expect_enqueued
):
    from app.scheduler import task_scheduler
    from app.tasks import chat_turns

    calls = []
    monkeypatch.setattr(chat_turns, "enqueue_chat_topic_labeller", lambda: calls.append(1))
    _pin_env(monkeypatch, chat_topic_labeller_enabled=env_value)
    save(chat_topic_labeller_enabled=db_value)

    task_scheduler._chat_topic_labeller_tick()

    assert len(calls) == expect_enqueued


# --------------------------------------------------------------------------- connector


def test_verify_token_uses_the_db_held_secret_and_connection_id(db, save):
    from app.services.analytics_connector import access
    from tests.analytics_connector import _helpers as h

    save(connector_ss_to_product_secret=h.SS_TO_PRODUCT, connector_connection_id=h.CONNECTION_ID)
    token = h.call_token("zzt-sub", "GET", "/api/v1/connector/manifest")

    claims = access.verify_token(token)

    assert claims["connection_id"] == h.CONNECTION_ID


def test_verify_token_is_503_when_nothing_is_configured_anywhere(db):
    from app.services.analytics_connector import access
    from tests.analytics_connector import _helpers as h

    token = h.call_token("zzt-sub", "GET", "/api/v1/connector/manifest")

    with pytest.raises(access.ConnectorError) as exc:
        access.verify_token(token)
    assert exc.value.status_code == 503


def test_consent_is_configured_when_the_four_values_are_only_in_the_db(db, save):
    from app.services.analytics_connector import consent
    from tests.analytics_connector import _helpers as h

    assert not consent._configured()

    save(
        connector_ss_to_product_secret=h.SS_TO_PRODUCT,
        connector_product_to_ss_secret=h.PRODUCT_TO_SS,
        connector_connection_id=h.CONNECTION_ID,
        connector_ss_callback_url=h.SS_CALLBACK,
    )

    assert consent._configured()


def test_consent_verify_request_accepts_a_request_signed_with_the_db_secret(db, save):
    from app.services.analytics_connector import consent
    from tests.analytics_connector import _helpers as h

    save(
        connector_ss_to_product_secret=h.SS_TO_PRODUCT,
        connector_product_to_ss_secret=h.PRODUCT_TO_SS,
        connector_connection_id=h.CONNECTION_ID,
        connector_ss_callback_url=h.SS_CALLBACK,
    )
    req, claims = h.consent_request()

    assert consent.verify_request(req)["request_id"] == claims["request_id"]


# --------------------------------------------------------------------------- read-only engine


def _dsn() -> str:
    return f"postgresql://zzt_ro_{uuid.uuid4().hex[:8]}:pw@localhost:5432/sorento_test"


def _url(engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def test_ro_engine_is_none_with_no_dsn_anywhere(db):
    from app.services.analytics_connector import datasets

    assert datasets._ro_engine() is None


def test_ro_engine_follows_the_dsn_saved_in_the_db_one_engine_per_dsn(db, save):
    from app.services.analytics_connector import datasets

    first, second = _dsn(), _dsn()
    engines = []
    try:
        assert datasets._ro_engine() is None

        save(analytics_ro_database_url=first)
        e1 = datasets._ro_engine()
        engines.append(e1)
        assert e1 is not None and _url(e1) == first
        assert datasets._ro_engine() is e1  # cached per DSN

        save(analytics_ro_database_url=second)
        e2 = datasets._ro_engine()
        engines.append(e2)
        assert e2 is not None and e2 is not e1 and _url(e2) == second
    finally:
        for e in engines:
            if e is not None:
                e.dispose()


def test_a_replaced_ro_engine_is_disposed(db, save, monkeypatch):
    """Security review L2: the old pool (old password / host) is closed when the DSN changes."""
    from app.services.analytics_connector import datasets

    save(analytics_ro_database_url=_dsn())
    first = datasets._ro_engine()
    disposed = []
    monkeypatch.setattr(first, "dispose", lambda *a, **k: disposed.append(1))
    try:
        save(analytics_ro_database_url=_dsn())
        second = datasets._ro_engine()
        assert second is not first and disposed == [1]
        save(analytics_ro_database_url="")
        assert datasets._ro_engine() is None
    finally:
        datasets._ro_current.clear()
