"""DO-BACKFILL-SETTING: "Assume DOs delivered before" moves from env into Settings > Integrations.

Owner, 15 Oct 2026: "can this be a frontend config, I really don't want to set things in env and
need to restart the prod services". The DO-HISTORY cutover (`DO_ASSUME_DELIVERED_BEFORE`, read once
per process) becomes a runtime setting (`app/services/runtime_settings.py`): a saved value wins, the
env var is the fallback while nothing is saved, and the ingest reads it per batch, so a save takes
effect on the next push with no restart.

Postgres `blank_session()` only (through the DO ingest fixture's `env`).
"""
from __future__ import annotations

import copy
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.order import OrderStatus
from tests._pg_fixture import TEST_PREFIX, blank_session
from tests.test_ingest_autocount_do_grn import (  # noqa: F401 - env is a fixture
    do_records,
    env,
)

NAME = "do_assume_delivered_before"
ENDPOINT = "/api/v1/user-management/settings/runtime"
SECRETS_SLUG = "user_management.settings.secrets"
EDIT_SLUG = "user_management.settings.edit"
CUTOVER = date(2026, 7, 1)


@pytest.fixture(autouse=True)
def no_env_cutover(monkeypatch):
    """No env fallback unless a test pins one."""
    monkeypatch.setattr(settings, NAME, None, raising=False)


def _dated(rec: dict, day: str) -> dict:
    out = copy.deepcopy(rec)
    out["DocDate"] = f"{day}T00:00:00"
    return out


def _delivered(db) -> str:
    row = db.execute(
        select(OrderStatus).where(OrderStatus.status_code.in_(["delivered", "DELIVERED"]))
    ).scalars().first()
    if row is None:
        row = OrderStatus(status_code="delivered", status_name="Delivered")
        db.add(row)
        db.commit()
    return str(row.id)


def _user(db):
    from app.models.user import User

    user = User(
        id=str(uuid.uuid4()),
        email=f"{TEST_PREFIX}-dobs-{uuid.uuid4().hex[:6]}@zzt.test".lower(),
        name=f"{TEST_PREFIX} DOBS Actor",
        avatar_storage_provider="s3",
    )
    db.add(user)
    db.commit()
    return user


# --------------------------------------------------------------------------- the resolver


def test_the_cutover_is_a_runtime_setting_and_not_a_secret():
    from app.services import runtime_settings as rs

    assert NAME in rs.FIELDS
    assert NAME not in rs.SECRET_FIELDS


def test_a_saved_date_wins_over_env_and_clearing_it_falls_back(monkeypatch):
    from app.services import runtime_settings as rs

    env_cutover = date(2025, 1, 1)
    with blank_session() as db:
        actor = _user(db)
        monkeypatch.setattr(settings, NAME, env_cutover, raising=False)
        assert rs.get(NAME, db=db) == env_cutover

        rs.update(db, {NAME: "2026-07-01"}, actor.id)
        assert rs.get(NAME, db=db) == CUTOVER

        rs.update(db, {NAME: ""}, actor.id)
        assert rs.get(NAME, db=db) == env_cutover

        monkeypatch.setattr(settings, NAME, None, raising=False)
        assert rs.get(NAME, db=db) is None


def test_describe_shows_the_saved_date_as_iso_text():
    from app.services import runtime_settings as rs

    with blank_session() as db:
        actor = _user(db)
        assert rs.describe(db)[NAME] == {
            "source": "unset", "secret": False, "value": None, "masked": None,
            "unreadable": False,
        }
        rs.update(db, {NAME: "2026-07-01"}, actor.id)
        field = rs.describe(db)[NAME]
        assert field["source"] == "db" and field["value"] == "2026-07-01"


# --------------------------------------------------------------------------- the ingest, no restart


def test_a_saved_date_applies_to_the_next_push_and_clearing_it_stops_it(env):
    """Brief step 2: set the date, push an old DO -> delivered; clear it, push another -> new.
    Same process throughout: no restart, no env."""
    from app.services import runtime_settings as rs

    delivered = _delivered(env.db)
    actor = _user(env.db)

    rs.update(env.db, {NAME: "2026-07-01"}, actor.id)
    env.push_do([_dated(do_records()[0], "2024-03-05")])
    first = env.order(900001)
    assert str(first.order_status_id) == delivered
    assert first.tracking_assumed_at is not None and first.actual_delivery_date is None

    rs.update(env.db, {NAME: None}, actor.id)
    env.push_do([_dated(do_records()[1], "2024-03-05")])
    second = env.order(900002)
    assert str(second.order_status_id) == env.new_status
    assert second.tracking_assumed_at is None


def test_a_saved_date_overrides_the_env_cutover(env, monkeypatch):
    """The env var is only the fallback: an earlier saved date keeps a 2024 DO as new even
    with env still at 2026-07-01."""
    from app.services import runtime_settings as rs

    _delivered(env.db)
    monkeypatch.setattr(settings, NAME, CUTOVER, raising=False)
    rs.update(env.db, {NAME: "2024-01-01"}, _user(env.db).id)

    env.push_do([_dated(do_records()[0], "2024-03-05")])

    o = env.order(900001)
    assert str(o.order_status_id) == env.new_status
    assert o.tracking_assumed_at is None


def test_the_env_cutover_still_applies_while_nothing_is_saved(env, monkeypatch):
    delivered = _delivered(env.db)
    monkeypatch.setattr(settings, NAME, CUTOVER, raising=False)

    env.push_do([_dated(do_records()[0], "2024-03-05")])

    assert str(env.order(900001).order_status_id) == delivered


# --------------------------------------------------------------------------- the endpoint


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user
    from app.main import app
    from app.services.user_service import UserPermissionService

    with blank_session() as db:
        actor = _user(db)
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
        try:
            yield TestClient(app), allow, db, actor
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)


def test_put_saves_the_date_and_writes_one_audit_row(api):
    from app.models.audit import AuditLog

    client, allow, db, actor = api
    allow.add(SECRETS_SLUG)

    resp = client.put(ENDPOINT, json={NAME: "2026-07-01"})

    assert resp.status_code == 200, resp.text
    field = resp.json()["fields"][NAME]
    assert field == {"source": "db", "secret": False, "value": "2026-07-01", "masked": None,
                     "unreadable": False}
    db.expire_all()
    rows = db.query(AuditLog).filter(AuditLog.event == "RUNTIME_SETTINGS").all()
    assert len(rows) == 1 and rows[0].user_id == actor.id
    assert rows[0].new_values["changes"] == {NAME: {"old": None, "new": "2026-07-01"}}

    cleared = client.put(ENDPOINT, json={NAME: None})
    assert cleared.status_code == 200
    assert cleared.json()["fields"][NAME]["source"] == "unset"


@pytest.mark.parametrize("bad", ["01/07/2026", "2026-13-01", "soon", 20260701, True])
def test_put_refuses_anything_but_an_iso_date(api, bad):
    client, allow, _db, _actor = api
    allow.add(SECRETS_SLUG)

    resp = client.put(ENDPOINT, json={NAME: bad})

    assert resp.status_code == 422
    assert NAME in resp.text


def test_put_refuses_a_date_in_the_future(api):
    """A future cutover would land today's live DOs delivered with no tracking."""
    client, allow, _db, _actor = api
    allow.add(SECRETS_SLUG)

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    resp = client.put(ENDPOINT, json={NAME: tomorrow})

    assert resp.status_code == 422
    assert NAME in resp.text
    assert client.put(ENDPOINT, json={NAME: date.today().isoformat()}).status_code == 200


@pytest.mark.parametrize("granted", [set(), {EDIT_SLUG, "user_management.settings.view"}])
def test_put_needs_the_integration_secrets_slug(api, granted):
    client, allow, db, _actor = api
    allow.update(granted)

    resp = client.put(ENDPOINT, json={NAME: "2026-07-01"})

    assert resp.status_code == 403
    from app.services import runtime_settings as rs

    assert rs.get(NAME, db=db) is None


def test_get_says_whether_a_delivered_order_status_exists(api):
    """Brief step 3: without a `delivered` status the ingest only logs a warning, so the screen
    must say so before the backfill runs."""
    client, allow, db, _actor = api
    allow.add(SECRETS_SLUG)
    db.query(OrderStatus).filter(OrderStatus.status_code.ilike("delivered")).delete(
        synchronize_session=False)
    db.commit()

    assert client.get(ENDPOINT).json()["delivered_order_status_exists"] is False

    _delivered(db)
    assert client.get(ENDPOINT).json()["delivered_order_status_exists"] is True
