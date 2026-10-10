"""SETTINGS-IN-DB-SORENTO: the runtime settings resolver and its endpoint (Phase 2, red first).

Plan: documentation/plans/system/PLAN-settings-in-db-sorento.md ("Contract (Phase 2)").
UAC:  documentation/plans/system/settings-in-db-sorento-acceptance-criteria.md (AC-1 .. AC-5, AC-7, AC-8).

Postgres `blank_session()` only. Env fallbacks are pinned through `app.config.settings`.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests._pg_fixture import TEST_PREFIX, blank_session

ENDPOINT = "/api/v1/user-management/settings/runtime"
SECRETS_SLUG = "user_management.settings.secrets"
EDIT_SLUG = "user_management.settings.edit"

SECRET_20 = "test-secret-not-real-0000"  # made up, never a real key; last four "0000"
SECRET_8 = "abcd1234"
ENV_VALUE = "zzt-env-fallback-value-0001"

ALL_FIELDS = [
    "chat_topic_labeller_enabled",
    "connector_ss_to_product_secret",
    "connector_product_to_ss_secret",
    "connector_connection_id",
    "connector_ss_callback_url",
    "analytics_ro_database_url",
    "respond_webhook_secret",
]
SECRET_FIELDS = [
    "connector_ss_to_product_secret",
    "connector_product_to_ss_secret",
    "analytics_ro_database_url",
    "respond_webhook_secret",
]


def _pin_env(monkeypatch, **values):
    """Set the env fallback on `settings`; undeclared fields go through __dict__ so a red run
    fails on the missing feature, not here."""
    from app.config import settings

    for name in ALL_FIELDS:
        value = values.get(name)
        if name in type(settings).model_fields:
            monkeypatch.setattr(settings, name, value, raising=False)
        else:
            monkeypatch.setitem(settings.__dict__, name, value)


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No env fallback unless a test pins one."""
    _pin_env(monkeypatch)


@pytest.fixture
def actor(db):
    from app.models.user import User

    user = User(
        id=str(uuid.uuid4()),
        email=f"{TEST_PREFIX}-rs-actor-{uuid.uuid4().hex[:6]}@zzt.test".lower(),
        name=f"{TEST_PREFIX} RS Actor",
        avatar_storage_provider="s3",
    )
    db.add(user)
    db.commit()
    return user


def _row(db):
    from app.models.user import SystemSetting

    db.expire_all()
    return db.query(SystemSetting).first()


def _audit_rows(db):
    from app.models.audit import AuditLog

    db.expire_all()
    return db.query(AuditLog).filter(AuditLog.event == "RUNTIME_SETTINGS").all()


# --------------------------------------------------------------------------- registry


def test_the_secrets_slug_is_registered():
    from app.rbac.permission_registry import PERMISSION_REGISTRY

    assert SECRETS_SLUG in {p["slug"] for p in PERMISSION_REGISTRY}


# --------------------------------------------------------------------------- AC-3 precedence


def test_fields_and_secret_fields_match_the_contract():
    from app.services import runtime_settings as rs

    assert list(rs.FIELDS) == ALL_FIELDS
    assert set(rs.SECRET_FIELDS) == set(SECRET_FIELDS)


@pytest.mark.parametrize(
    "name, db_value",
    [
        ("respond_webhook_secret", SECRET_20),
        ("connector_connection_id", "zzt-conn-from-db"),
    ],
)
def test_db_value_wins_over_env(db, actor, monkeypatch, name, db_value):
    from app.services import runtime_settings as rs

    _pin_env(monkeypatch, **{name: ENV_VALUE})
    assert rs.get(name, db=db) == ENV_VALUE  # env fallback while the DB holds nothing

    rs.update(db, {name: db_value}, actor.id)

    assert rs.get(name, db=db) == db_value


@pytest.mark.parametrize(
    "name, db_value",
    [
        ("respond_webhook_secret", SECRET_20),
        ("connector_connection_id", "zzt-conn-from-db"),
    ],
)
def test_cleared_db_value_falls_back_to_env_then_none(db, actor, monkeypatch, name, db_value):
    from app.services import runtime_settings as rs

    _pin_env(monkeypatch, **{name: ENV_VALUE})
    rs.update(db, {name: db_value}, actor.id)
    rs.update(db, {name: ""}, actor.id)
    assert rs.get(name, db=db) == ENV_VALUE

    _pin_env(monkeypatch)  # env gone too
    assert rs.get(name, db=db) is None


def test_labeller_bool_precedence_and_default_false(db, actor, monkeypatch):
    from app.services import runtime_settings as rs

    assert rs.get("chat_topic_labeller_enabled", db=db) is False  # neither: False, not None

    _pin_env(monkeypatch, chat_topic_labeller_enabled=True)
    assert rs.get("chat_topic_labeller_enabled", db=db) is True  # env fallback

    rs.update(db, {"chat_topic_labeller_enabled": False}, actor.id)
    assert rs.get("chat_topic_labeller_enabled", db=db) is False  # DB False beats env True

    rs.update(db, {"chat_topic_labeller_enabled": None}, actor.id)  # None clears back to env
    assert rs.get("chat_topic_labeller_enabled", db=db) is True


def test_get_without_db_opens_its_own_session(db, actor, monkeypatch):
    from app.services import runtime_settings as rs

    rs.update(db, {"connector_connection_id": "zzt-own-session"}, actor.id)

    class _Keep:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, item):
            return getattr(self._inner, item)

        def close(self):
            pass

    monkeypatch.setattr(rs, "SessionLocal", lambda: _Keep(db))

    assert rs.get("connector_connection_id") == "zzt-own-session"


# --------------------------------------------------------------------------- AC-2 stored encrypted


def test_a_secret_is_stored_encrypted_not_plain(db, actor):
    from app.services import runtime_settings as rs
    from app.utils.field_encryption import decrypt_secret

    rs.update(db, {"respond_webhook_secret": SECRET_20}, actor.id)

    raw = _row(db).respond_webhook_secret_ciphertext
    assert raw and raw != SECRET_20 and SECRET_20 not in raw
    assert decrypt_secret(raw) == SECRET_20


def test_omitted_key_keeps_and_empty_or_none_clears(db, actor):
    from app.services import runtime_settings as rs

    rs.update(db, {"respond_webhook_secret": SECRET_20, "connector_connection_id": "zzt-conn"}, actor.id)

    rs.update(db, {"connector_connection_id": "zzt-conn-2"}, actor.id)  # secret omitted
    assert rs.get("respond_webhook_secret", db=db) == SECRET_20

    rs.update(db, {"respond_webhook_secret": ""}, actor.id)
    assert rs.get("respond_webhook_secret", db=db) is None
    assert _row(db).respond_webhook_secret_ciphertext is None

    rs.update(db, {"connector_connection_id": None}, actor.id)
    assert _row(db).connector_connection_id is None


def test_update_creates_the_singleton_row_when_absent(db, actor):
    from app.models.user import SystemSetting
    from app.services import runtime_settings as rs

    assert db.query(SystemSetting).first() is None

    rs.update(db, {"connector_connection_id": "zzt-conn"}, actor.id)

    assert db.query(SystemSetting).count() == 1


# --------------------------------------------------------------------------- AC-1 describe


def test_describe_masks_secrets_and_shows_non_secrets(db, actor, monkeypatch):
    from app.services import runtime_settings as rs

    rs.update(
        db,
        {
            "respond_webhook_secret": SECRET_20,
            "connector_ss_to_product_secret": SECRET_8,
            "connector_connection_id": "zzt-conn-visible",
        },
        actor.id,
    )
    _pin_env(monkeypatch, connector_ss_callback_url="https://env.example/cb")

    out = rs.describe(db)

    assert set(out) == set(ALL_FIELDS)
    assert out["respond_webhook_secret"]["masked"] == "****0000"
    assert out["respond_webhook_secret"]["source"] == "db"
    assert out["respond_webhook_secret"]["secret"] is True
    assert out["respond_webhook_secret"]["value"] is None
    assert out["connector_ss_to_product_secret"]["masked"] == "****"  # 8 chars or fewer
    assert out["connector_product_to_ss_secret"]["masked"] is None
    assert out["connector_product_to_ss_secret"]["source"] == "unset"
    assert out["connector_connection_id"]["value"] == "zzt-conn-visible"
    assert out["connector_connection_id"]["secret"] is False
    assert out["connector_connection_id"]["source"] == "db"
    assert out["connector_ss_callback_url"]["value"] == "https://env.example/cb"
    assert out["connector_ss_callback_url"]["source"] == "env"
    for name in ALL_FIELDS:
        assert out[name]["unreadable"] is False
    dumped = json.dumps(out)
    assert SECRET_20 not in dumped and SECRET_8 not in dumped


def test_describe_masks_an_env_secret_too(db, monkeypatch):
    from app.services import runtime_settings as rs

    _pin_env(monkeypatch, respond_webhook_secret=SECRET_20)

    out = rs.describe(db)

    assert out["respond_webhook_secret"]["source"] == "env"
    assert out["respond_webhook_secret"]["masked"] == "****0000"
    assert SECRET_20 not in json.dumps(out)


# --------------------------------------------------------------------------- AC-7 unreadable


def test_unreadable_ciphertext_fails_closed_and_never_raises(db, actor, monkeypatch):
    """Security review M2: an undecryptable saved secret does NOT fall back to env, which may
    hold the very secret the saved one replaced."""
    from app.services import runtime_settings as rs

    rs.update(db, {"respond_webhook_secret": SECRET_20}, actor.id)
    row = _row(db)
    row.respond_webhook_secret_ciphertext = "this-is-not-a-fernet-token"
    db.commit()
    _pin_env(monkeypatch, respond_webhook_secret=ENV_VALUE)

    assert rs.get("respond_webhook_secret", db=db) is None
    out = rs.describe(db)
    assert out["respond_webhook_secret"]["unreadable"] is True
    assert out["respond_webhook_secret"]["source"] == "db"
    assert out["respond_webhook_secret"]["masked"] is None

    rs.update(db, {"respond_webhook_secret": ""}, actor.id)  # clearing it is what re-opens env
    assert rs.get("respond_webhook_secret", db=db) == ENV_VALUE


# --------------------------------------------------------------------------- AC-5 audit


def test_one_audit_row_names_actor_and_hides_secret_values(db, actor):
    from app.models.audit import AuditLog
    from app.services import runtime_settings as rs

    rs.update(
        db,
        {
            "respond_webhook_secret": SECRET_20,
            "connector_connection_id": "zzt-conn-1",
            "chat_topic_labeller_enabled": True,
        },
        actor.id,
    )

    rows = _audit_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.entity_type == "system_settings"
    assert row.action == "UPDATE"
    assert row.user_id == actor.id
    new = row.new_values
    assert new["secrets_set"] == ["respond_webhook_secret"]
    assert new["secrets_cleared"] == []
    assert new["changes"]["connector_connection_id"] == {"old": None, "new": "zzt-conn-1"}
    assert new["changes"]["chat_topic_labeller_enabled"] == {"old": None, "new": True}
    assert "respond_webhook_secret" not in new["changes"]

    everything = json.dumps(
        [[r.old_values, r.new_values, r.description] for r in db.query(AuditLog).all()], default=str
    )
    assert SECRET_20 not in everything


def test_clearing_a_secret_and_changing_a_value_is_audited_old_to_new(db, actor):
    from app.services import runtime_settings as rs

    rs.update(db, {"respond_webhook_secret": SECRET_20, "connector_connection_id": "zzt-conn-1"}, actor.id)
    rs.update(db, {"respond_webhook_secret": "", "connector_connection_id": "zzt-conn-2"}, actor.id)

    rows = sorted(_audit_rows(db), key=lambda r: r.changed_at)
    assert len(rows) == 2
    new = rows[1].new_values
    assert new["secrets_cleared"] == ["respond_webhook_secret"]
    assert new["secrets_set"] == []
    assert new["changes"]["connector_connection_id"] == {"old": "zzt-conn-1", "new": "zzt-conn-2"}


def test_no_audit_row_when_nothing_changed(db, actor):
    from app.services import runtime_settings as rs

    rs.update(db, {"connector_connection_id": "zzt-conn-1"}, actor.id)
    before = len(_audit_rows(db))

    rs.update(db, {}, actor.id)  # nothing sent
    rs.update(db, {"connector_connection_id": "zzt-conn-1"}, actor.id)  # same value
    rs.update(db, {"respond_webhook_secret": ""}, actor.id)  # clearing what is not set

    assert len(_audit_rows(db)) == before == 1


# --------------------------------------------------------------------------- API (AC-1, AC-4, AC-8)


@pytest.fixture
def api(db, actor, monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user
    from app.main import app
    from app.services.user_service import UserPermissionService

    allow: set[str] = set()

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: {"id": actor.id, "email": actor.email}
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in allow,
    )
    client = TestClient(app)
    try:
        yield client, allow
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize("method", ["get", "put"])
def test_denied_without_any_permission(api, method):
    client, _allow = api
    resp = getattr(client, method)(ENDPOINT, **({"json": {}} if method == "put" else {}))
    assert resp.status_code == 403
    assert SECRETS_SLUG in str(resp.json())


@pytest.mark.parametrize("method", ["get", "put"])
def test_denied_with_only_the_settings_edit_slug(api, method):
    client, allow = api
    allow.add(EDIT_SLUG)
    allow.add("user_management.settings.view")
    resp = getattr(client, method)(ENDPOINT, **({"json": {}} if method == "put" else {}))
    assert resp.status_code == 403


def test_get_answers_with_the_slug_and_carries_the_webhook_hints(api, db, actor):
    from app.services import runtime_settings as rs

    client, allow = api
    allow.add(SECRETS_SLUG)
    rs.update(db, {"respond_webhook_secret": SECRET_20, "connector_connection_id": "zzt-conn"}, actor.id)

    resp = client.get(ENDPOINT)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body["fields"]) == set(ALL_FIELDS)
    assert body["respond_webhook_path"] == "/api/v1/public/respond/webhook"
    assert body["respond_signature_headers"] == ["x-respond-signature", "x-webhook-signature"]
    assert body["respond_shared_secret_header"] == "X-Respond-Webhook-Secret"
    assert body["fields"]["respond_webhook_secret"]["masked"] == "****0000"
    assert body["fields"]["respond_webhook_secret"]["source"] == "db"
    assert body["fields"]["connector_connection_id"]["value"] == "zzt-conn"
    assert body["fields"]["connector_product_to_ss_secret"]["source"] == "unset"
    assert SECRET_20 not in resp.text


def test_put_sets_a_secret_returns_the_same_shape_and_never_echoes_it(api, db):
    client, allow = api
    allow.add(SECRETS_SLUG)

    resp = client.put(ENDPOINT, json={"respond_webhook_secret": SECRET_20})

    assert resp.status_code == 200
    body = resp.json()
    assert body["fields"]["respond_webhook_secret"]["masked"] == "****0000"
    assert body["respond_webhook_path"] == "/api/v1/public/respond/webhook"
    assert SECRET_20 not in resp.text
    from app.utils.field_encryption import decrypt_secret

    assert decrypt_secret(_row(db).respond_webhook_secret_ciphertext) == SECRET_20
    assert SECRET_20 not in client.get(ENDPOINT).text


def test_put_records_the_calling_user_in_the_audit_row(api, db, actor):
    client, allow = api
    allow.add(SECRETS_SLUG)

    client.put(ENDPOINT, json={"connector_connection_id": "zzt-conn-api"})

    rows = _audit_rows(db)
    assert len(rows) == 1 and rows[0].user_id == actor.id


def test_callback_url_must_be_https(api):
    client, allow = api
    allow.add(SECRETS_SLUG)

    assert client.put(ENDPOINT, json={"connector_ss_callback_url": "http://x"}).status_code == 422
    ok = client.put(ENDPOINT, json={"connector_ss_callback_url": "https://ss.example/cb"})
    assert ok.status_code == 200
    assert ok.json()["fields"]["connector_ss_callback_url"]["value"] == "https://ss.example/cb"


def test_unknown_key_is_422(api):
    client, allow = api
    allow.add(SECRETS_SLUG)

    resp = client.put(ENDPOINT, json={"not_a_setting": "x"})

    assert resp.status_code == 422


def test_labeller_reads_unset_not_env_when_the_env_var_is_absent(db, monkeypatch):
    """The Settings default is False; with no env var set the switch is 'unset', not 'env'
    (found in the browser pass: a fresh install showed 'Server env')."""
    from app.config import settings
    from app.services import runtime_settings as rs

    monkeypatch.setattr(settings, "chat_topic_labeller_enabled", False, raising=False)

    out = rs.describe(db)["chat_topic_labeller_enabled"]

    assert out["source"] == "unset"
    assert out["value"] is False


def test_a_failed_read_fails_closed_for_secrets_only(monkeypatch):
    """M2: when system_settings cannot be read, a secret is unset (no env fallback) while a
    non-secret still uses env."""
    from app.services import runtime_settings as rs

    class _Broken:
        def query(self, *_a, **_k):
            raise RuntimeError("database down")

        def close(self):
            pass

    _pin_env(monkeypatch, respond_webhook_secret=ENV_VALUE, connector_connection_id="zzt-env-conn")
    monkeypatch.setattr(rs, "SessionLocal", lambda: _Broken())

    assert rs.get("respond_webhook_secret") is None
    assert rs.get("connector_connection_id") == "zzt-env-conn"


def test_sessionless_reads_are_cached_and_a_save_clears_the_cache(db, actor, monkeypatch):
    """H1: readers without a session (the connector preflight) read the database at most once per
    CACHE_TTL_SECONDS; a save in this process is visible at once."""
    from app.services import runtime_settings as rs

    opened = []

    class _Keep:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, item):
            return getattr(self._inner, item)

        def close(self):
            pass

    def _factory():
        opened.append(1)
        return _Keep(db)

    monkeypatch.setattr(rs, "SessionLocal", _factory)

    for _ in range(5):
        assert rs.get("connector_connection_id") is None
    assert len(opened) == 1

    rs.update(db, {"connector_connection_id": "zzt-after-save"}, actor.id)
    assert rs.get("connector_connection_id") == "zzt-after-save"


@pytest.mark.parametrize(
    "body",
    [
        {"analytics_ro_database_url": "postgresql+asyncpg://ro:ZztLeakPw0001@db/sorento"},
        {"respond_webhook_secret": "ZztLeakPw0001" + "x" * 1000},
        {"respond_webhook_secret": 12345678901, "connector_connection_id": "ZztLeakPw0001"},
    ],
)
def test_a_refused_value_never_reaches_the_logs(api, caplog, body):
    """Security review M1: the app's 422 handler logs Pydantic's `input`; a refusal here must
    name the field only, in the response and in every log line."""
    import logging

    client, allow = api
    allow.add(SECRETS_SLUG)
    caplog.set_level(logging.DEBUG)

    resp = client.put(ENDPOINT, json=body)

    assert resp.status_code == 422
    assert "ZztLeakPw0001" not in resp.text
    assert "12345678901" not in resp.text
    assert "ZztLeakPw0001" not in caplog.text
    assert "12345678901" not in caplog.text


def test_a_save_writes_exactly_one_system_settings_audit_row(db, actor):
    """Review finding 2 / AC-5: the generic audit listener adds no second (blank) row."""
    from app.models.audit import AuditLog
    from app.services import runtime_settings as rs

    rs.update(db, {"respond_webhook_secret": SECRET_20}, actor.id)

    db.expire_all()
    rows = db.query(AuditLog).filter(AuditLog.entity_type == "system_settings").all()
    assert [r.event for r in rows] == ["RUNTIME_SETTINGS"]


@pytest.mark.parametrize("path", ["/api/v1/user-management/settings/notifications", "/api/v1/user-management/settings/general"])
def test_older_settings_puts_never_echo_stored_secrets(api, db, actor, path):
    """Review blocker: the older PUTs returned the raw row, so any caller received every
    `*_ciphertext` column (and the SMTP password)."""
    from app.services import runtime_settings as rs

    client, allow = api
    allow.add(EDIT_SLUG)
    rs.update(db, {"respond_webhook_secret": SECRET_20}, actor.id)
    row = _row(db)
    row.smtp_password = "ZztSmtpPass0001"
    db.commit()
    cipher = row.respond_webhook_secret_ciphertext

    resp = client.put(path, json={})

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data, "the echo is still the settings row"
    assert not [k for k in data if k.endswith("_ciphertext")]
    assert "smtp_password" not in data
    assert cipher not in resp.text and "ZztSmtpPass0001" not in resp.text


def test_a_read_that_started_before_a_save_does_not_cache_the_old_value(db, actor, monkeypatch):
    """Security re-check N2: a sessionless read in flight while a save commits must not write
    its older snapshot back over the cleared cache."""
    from app.services import runtime_settings as rs

    class _Keep:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, item):
            return getattr(self._inner, item)

        def close(self):
            pass

    rs.update(db, {"connector_connection_id": "zzt-old"}, actor.id)
    real_snapshot = rs._snapshot

    def _slow_snapshot(row):
        snap = real_snapshot(row)  # the old value, read before the save below
        rs.update(db, {"connector_connection_id": "zzt-new"}, actor.id)
        return snap

    monkeypatch.setattr(rs, "SessionLocal", lambda: _Keep(db))
    monkeypatch.setattr(rs, "_snapshot", _slow_snapshot)
    assert rs.get("connector_connection_id") == "zzt-old"  # this one read is allowed to be old

    monkeypatch.setattr(rs, "_snapshot", real_snapshot)
    assert rs.get("connector_connection_id") == "zzt-new"  # but it was not cached


RESPOND_KEY_ONE = "k9/Fq+2Zr8LmW1x/Pt7+eD4uYc0sHn6vJb3aTq5oRg8="  # made up base64 keys
RESPOND_KEY_TWO = "Zx+7mP/2Qw9eLr4/Tn6+Ya1cVb8uKd3sHj5gFo0iEk4="


def test_put_takes_several_respond_signing_keys(api, db):
    client, allow = api
    allow.add(SECRETS_SLUG)

    resp = client.put(
        ENDPOINT, json={"respond_webhook_secret": f"{RESPOND_KEY_ONE}\n{RESPOND_KEY_TWO}\n"}
    )

    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("short", ["key", "message.received:", "x" * 23])
def test_put_refuses_a_respond_key_under_24_characters_without_echoing_it(api, short):
    client, allow = api
    allow.add(SECRETS_SLUG)

    resp = client.put(ENDPOINT, json={"respond_webhook_secret": f"{RESPOND_KEY_ONE}\n{short}"})

    assert resp.status_code == 422
    assert "respond_webhook_secret" in resp.text
    assert short not in resp.text
    assert RESPOND_KEY_ONE not in resp.text


def test_put_refuses_more_than_ten_respond_keys(api):
    client, allow = api
    allow.add(SECRETS_SLUG)
    keys = [f"zzt-made-up-signing-key-{i:04d}" for i in range(11)]

    assert client.put(ENDPOINT, json={"respond_webhook_secret": "\n".join(keys)}).status_code == 422
    ok = client.put(ENDPOINT, json={"respond_webhook_secret": "\n".join(keys[:10])})
    assert ok.status_code == 200, ok.text
